"""Trainer agent — real iterative sklearn training on session data (ENABLE_TRAINER=1).

SGDClassifier / SGDRegressor with partial_fit over N epochs; logs to W&B when
available; streams per-epoch train/val loss for training_update frames.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Literal

import numpy as np
import pandas as pd
import weave
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError
from sklearn.linear_model import SGDClassifier, SGDRegressor
from sklearn.metrics import log_loss, mean_squared_error
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler

from backend import config
from backend.agents.context import OrchestratorContext
from backend.agents.dataset_versions import current_version, get_working
from backend.agents.preprocessor import get_train_val_split
from backend.agents.parse import parse_json_object
from backend.agents.problem_type import _pick_target_column
from backend.agents import run_registry
from backend.tools import redis_state
from backend.tools.wandb_history import _summarize

log = logging.getLogger("hololab.agents.trainer")

ProblemType = Literal["classification", "regression", "unknown"]
N_EPOCHS = 50

# invscaling schedule: starts ~0.6 val log_loss, converges ~0.43 on titanic-scale data
_SGD_KW = dict(
    max_iter=1,
    tol=None,
    random_state=42,
    learning_rate="invscaling",
    eta0=0.001,
    power_t=0.25,
    alpha=0.0001,
)

EpochCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


def _infer_problem_type(series: pd.Series, n_rows: int) -> ProblemType:
    n_unique = int(series.nunique(dropna=True))
    if pd.api.types.is_bool_dtype(series):
        return "classification"
    if pd.api.types.is_object_dtype(series) or isinstance(series.dtype, pd.CategoricalDtype):
        return "classification"
    if pd.api.types.is_integer_dtype(series) and n_unique <= 20:
        return "classification"
    if pd.api.types.is_numeric_dtype(series):
        return "regression"
    return "unknown"


def _resolve_problem_type(sid: str, df: pd.DataFrame, target_col: str) -> ProblemType:
    cached = redis_state.get_scratch(sid, "problem_type") or {}
    pt = cached.get("problem_type")
    if pt in ("classification", "regression"):
        return pt
    return _infer_problem_type(df[target_col], len(df))


def _encode_features(df: pd.DataFrame, target_col: str) -> tuple[pd.DataFrame, pd.Series]:
    """One-hot + impute only — scaling happens after train/test split."""
    work = df.dropna(subset=[target_col]).copy()
    y = work[target_col]
    X = work.drop(columns=[target_col])
    X = pd.get_dummies(X, drop_first=False)
    for col in X.columns:
        if X[col].isna().any():
            if pd.api.types.is_numeric_dtype(X[col]):
                X[col] = X[col].fillna(X[col].median())
            else:
                X[col] = X[col].fillna(0)
    return X.astype(float), y


def _split_and_scale(
    X: pd.DataFrame,
    y: pd.Series,
    problem_type: ProblemType,
) -> tuple[np.ndarray, np.ndarray, pd.Series, pd.Series]:
    """train_test_split first; StandardScaler fit on train only, transform val."""
    stratify = y if problem_type == "classification" and y.nunique() > 1 else None
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42, stratify=stratify,
        )
    except ValueError:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42,
        )
    scaler = StandardScaler()
    X_train_arr = scaler.fit_transform(X_train)
    X_test_arr = scaler.transform(X_test)
    return X_train_arr, X_test_arr, y_train, y_test


def _trend_label(metrics: list[dict[str, Any]]) -> str:
    if len(metrics) < 2:
        return "converging"
    first_val = float(metrics[0].get("val_loss") or 0.0)
    last_val = float(metrics[-1].get("val_loss") or 0.0)
    if last_val < first_val * 0.95:
        return "converging"
    if last_val > first_val * 1.05:
        return "diverging"
    return "plateauing"


def _speech_from_run(
    problem_type: str, model_name: str, n_epochs: int,
    final_train: float, final_val: float, trend: str,
) -> str:
    return (
        f"Trained {model_name} for {n_epochs} epochs — "
        f"train loss {final_train:.2f}, val loss {final_val:.2f}, {trend}"
    )


def _wandb_init_kwargs(run_config: dict[str, Any]) -> dict[str, Any]:
    """Build wandb.init kwargs; entity matches weave when WANDB_ENTITY is set."""
    kwargs: dict[str, Any] = {
        "project": config.WANDB_PROJECT,
        "reinit": True,
        "config": run_config,
    }
    if config.WANDB_ENTITY:
        kwargs["entity"] = config.WANDB_ENTITY
    return kwargs


def _wandb_log_run(
    metrics: list[dict[str, Any]],
    run_config: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Returns (wandb_run_path, wandb_url) or (None, None) on failure."""
    try:
        import wandb

        # Do NOT call wandb.login(key=...) — wandb_v1 keys (86 chars) are read
        # from WANDB_API_KEY env by wandb.init(); explicit login validates the
        # legacy 40-char format and fails.
        run = wandb.init(**_wandb_init_kwargs(run_config))
        for row in metrics:
            wandb.log({
                "train_loss": row["train_loss"],
                "val_loss": row["val_loss"],
                "epoch": row["epoch"],
                "step": row["step"],
            })
        url = getattr(run, "url", None)
        path = f"{run.entity}/{run.project}/{run.id}" if run.entity else f"{run.project}/{run.id}"
        wandb.finish()
        return path, url
    except Exception as e:  # noqa: BLE001
        log.warning("wandb logging failed (%s) — continuing locally", e)
        return None, None


@weave.op()
def op_run_training(sid: str, dataset_name: str, df: pd.DataFrame | None) -> dict[str, Any]:
    """Iterative partial_fit training; returns per-epoch metrics + run metadata."""
    version = 0
    if config.ENABLE_PREPROCESSOR:
        w = get_working(sid)
        if w is not None:
            version, dataset_name, df = w[0], w[1], w[2]
        else:
            version = current_version(sid) or 0

    if df is None or df.empty:
        return {"error": "no dataset loaded", "version": version}

    target_col = _pick_target_column(df)
    problem_type = _resolve_problem_type(sid, df, target_col)
    if problem_type == "unknown":
        return {
            "error": f"could not determine problem type for target '{target_col}'",
            "version": version,
            "target_column": target_col,
        }

    try:
        split = get_train_val_split(sid, version) if config.ENABLE_PREPROCESSOR else None
        if split is not None:
            train_df = pd.DataFrame(
                split["train"]["records"], columns=split["train"]["columns"],
            )
            val_df = pd.DataFrame(
                split["val"]["records"], columns=split["val"]["columns"],
            )
            target_col = str(split.get("target") or target_col)
            X_train_arr = train_df.drop(columns=[target_col]).astype(float).values
            X_test_arr = val_df.drop(columns=[target_col]).astype(float).values
            y_train = train_df[target_col]
            y_test = val_df[target_col]
            problem_type = _resolve_problem_type(sid, train_df, target_col)
        else:
            X, y = _encode_features(df, target_col)
            X_train_arr, X_test_arr, y_train, y_test = _split_and_scale(X, y, problem_type)
    except Exception as e:  # noqa: BLE001
        return {"error": f"feature prep failed: {e}", "version": version}

    if len(X_train_arr) < 6:
        return {"error": f"not enough train rows after prep ({len(X_train_arr)})", "version": version}

    metrics: list[dict[str, Any]] = []
    model_name = ""

    if problem_type == "classification":
        model_name = "SGD logistic regression"
        le = LabelEncoder()
        y_train_enc = le.fit_transform(y_train)
        y_test_enc = le.transform(y_test)
        classes = np.unique(y_train_enc)
        clf = SGDClassifier(loss="log_loss", **_SGD_KW)
        for epoch in range(N_EPOCHS):
            clf.partial_fit(X_train_arr, y_train_enc, classes=classes)
            train_proba = clf.predict_proba(X_train_arr)
            val_proba = clf.predict_proba(X_test_arr)
            # sklearn.metrics.log_loss: mean negative log-likelihood per sample (normalize=True default)
            train_loss = float(log_loss(y_train_enc, train_proba, labels=classes))
            val_loss = float(log_loss(y_test_enc, val_proba, labels=classes))
            metrics.append({
                "step": epoch,
                "epoch": epoch,
                "train_loss": round(train_loss, 4),
                "val_loss": round(val_loss, 4),
            })
    else:
        model_name = "SGD regression"
        reg = SGDRegressor(**_SGD_KW)
        y_train_arr = np.asarray(y_train, dtype=float)
        y_test_arr = np.asarray(y_test, dtype=float)
        for epoch in range(N_EPOCHS):
            reg.partial_fit(X_train_arr, y_train_arr)
            train_pred = reg.predict(X_train_arr)
            val_pred = reg.predict(X_test_arr)
            train_loss = float(mean_squared_error(y_train_arr, train_pred))
            val_loss = float(mean_squared_error(y_test_arr, val_pred))
            metrics.append({
                "step": epoch,
                "epoch": epoch,
                "train_loss": round(train_loss, 4),
                "val_loss": round(val_loss, 4),
            })

    summary = _summarize(metrics)
    final_train = float(summary.get("final_train_loss") or metrics[-1]["train_loss"])
    final_val = float(summary.get("final_val_loss") or metrics[-1]["val_loss"])
    trend = _trend_label(metrics)

    run_config = {
        "model": model_name,
        "dataset": dataset_name,
        "target": target_col,
        "problem_type": problem_type,
        "version": version,
        "epochs": N_EPOCHS,
        "lr": "invscaling",
        "eta0": 0.001,
        "power_t": 0.25,
    }
    wandb_path, wandb_url = _wandb_log_run(metrics, run_config)

    run_id = wandb_path or f"hololab-session-{sid[:8]}-{uuid.uuid4().hex[:8]}"
    history = {
        "run_id": run_id,
        "source": "trainer",
        "config": run_config,
        "metrics": metrics,
        "summary": summary,
        "wandb_url": wandb_url,
        "problem_type": problem_type,
        "target_column": target_col,
        "version": version,
        "dataset_name": dataset_name,
        "model_name": model_name,
        "n_epochs": N_EPOCHS,
        "final_train_loss": final_train,
        "final_val_loss": final_val,
        "trend": trend,
        "trained_at": time.time(),
    }

    redis_state.set_scratch(sid, "trainer_run", history)
    run_registry.set_active(sid, run_id)

    return {
        "ok": True,
        "run_id": run_id,
        "wandb_url": wandb_url,
        "problem_type": problem_type,
        "target_column": target_col,
        "version": version,
        "model_name": model_name,
        "n_epochs": N_EPOCHS,
        "final_train_loss": final_train,
        "final_val_loss": final_val,
        "trend": trend,
        "metrics": metrics,
        "history": history,
    }


class TrainerOutput(BaseModel):
    speech: str
    run_id: str = ""
    problem_type: ProblemType = "unknown"
    target_column: str | None = None
    version: int = 0
    model_name: str = ""
    n_epochs: int = 0
    final_train_loss: float = 0.0
    final_val_loss: float = 0.0
    trend: str = ""
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    wandb_url: str | None = None


_EMPTY = TrainerOutput(speech="I couldn't train — load a dataset and try again.")


@function_tool
async def run_training_tool(ctx: RunContextWrapper[OrchestratorContext]) -> dict[str, Any]:
    """Run iterative sklearn training on the session dataset."""
    return op_run_training(ctx.context.session_id, ctx.context.dataset_name, ctx.context.df)


def _openai_model() -> OpenAIChatCompletionsModel:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; trainer agent requires OpenAI")
    return OpenAIChatCompletionsModel(
        model=config.FALLBACK_MODEL,
        openai_client=AsyncOpenAI(api_key=config.OPENAI_API_KEY),
    )


_SYSTEM_PROMPT = """\
You are the trainer agent. Call `run_training_tool` ONCE first — it runs real
iterative sklearn training and returns per-epoch losses.

Then emit JSON inside ```json ... ``` using ONLY numbers from the tool:

  {
    "speech": "<one sentence citing epochs, train loss, val loss, trend>",
    "run_id": "<from tool>",
    "problem_type": "classification" | "regression",
    "target_column": "<from tool>",
    "version": <int>,
    "model_name": "<from tool>",
    "n_epochs": <int>,
    "final_train_loss": <float>,
    "final_val_loss": <float>,
    "trend": "converging" | "plateauing" | "diverging"
  }

If the tool returns error, speech must say "couldn't train because <reason>".
Do NOT invent metrics.
"""


def _make_agent(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    return Agent[OrchestratorContext](
        name="trainer",
        handoff_description=(
            "Use when the user wants to train a model on the cleaned session "
            "data: train the model, start training, fit the model."
        ),
        instructions=_SYSTEM_PROMPT,
        model=model,
        model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
        tools=[run_training_tool],
    )


_agent: Agent[OrchestratorContext] | None = None


def get_agent() -> Agent[OrchestratorContext]:
    global _agent
    if _agent is None:
        _agent = _make_agent(_openai_model())
    return _agent


def get_fallback_agent() -> Agent[OrchestratorContext]:
    return get_agent()


def _output_from_result(result: dict[str, Any]) -> TrainerOutput:
    if result.get("error"):
        return TrainerOutput(
            speech=f"couldn't train because {result['error']}",
            version=int(result.get("version") or 0),
            target_column=result.get("target_column"),
        )
    return TrainerOutput(
        speech=_speech_from_run(
            str(result.get("problem_type") or "unknown"),
            str(result.get("model_name") or "SGD model"),
            int(result.get("n_epochs") or N_EPOCHS),
            float(result.get("final_train_loss") or 0.0),
            float(result.get("final_val_loss") or 0.0),
            str(result.get("trend") or "converging"),
        ),
        run_id=str(result.get("run_id") or ""),
        problem_type=result.get("problem_type") or "unknown",
        target_column=result.get("target_column"),
        version=int(result.get("version") or 0),
        model_name=str(result.get("model_name") or ""),
        n_epochs=int(result.get("n_epochs") or 0),
        final_train_loss=float(result.get("final_train_loss") or 0.0),
        final_val_loss=float(result.get("final_val_loss") or 0.0),
        trend=str(result.get("trend") or ""),
        metrics=list(result.get("metrics") or []),
        wandb_url=result.get("wandb_url"),
    )


async def _emit_epoch(
    on_epoch: EpochCallback | None,
    row: dict[str, Any],
    run_id: str,
) -> None:
    if on_epoch is None:
        return
    payload = {**row, "run_id": run_id}
    result = on_epoch(payload)
    if result is not None:
        await result


@weave.op()
async def run_for_query(
    sid: str,
    text: str,
    df: pd.DataFrame | None,
    dataset_name: str,
    *,
    on_epoch: EpochCallback | None = None,
) -> TrainerOutput:
    """Deterministic training path with optional per-epoch callback."""
    import asyncio

    try:
        result = await asyncio.to_thread(op_run_training, sid, dataset_name, df)
        if result.get("error"):
            return _output_from_result(result)
        run_id = str(result.get("run_id") or "")
        for row in result.get("metrics") or []:
            await _emit_epoch(on_epoch, row, run_id)
        return _output_from_result(result)
    except Exception as e:  # noqa: BLE001
        log.warning("trainer run failed: %s", e)
        return TrainerOutput(speech=f"couldn't train because {e}")


def _parse_output(raw: str, ctx: OrchestratorContext) -> TrainerOutput:
    data = parse_json_object(raw)
    if data is None:
        return _EMPTY
    try:
        out = TrainerOutput.model_validate(data)
    except ValidationError:
        out = TrainerOutput(
            speech=str(data.get("speech") or raw)[:300],
            run_id=str(data.get("run_id") or ""),
            problem_type=data.get("problem_type") or "unknown",
        )
    if not out.metrics:
        cached = op_run_training(ctx.session_id, ctx.dataset_name, ctx.df)
        if cached.get("metrics"):
            out.metrics = cached["metrics"]
            out.run_id = str(cached.get("run_id") or out.run_id)
    if not out.speech or out.speech == _EMPTY.speech:
        if out.final_train_loss or out.metrics:
            out.speech = _speech_from_run(
                out.problem_type, out.model_name or "SGD model",
                out.n_epochs or len(out.metrics), out.final_train_loss,
                out.final_val_loss, out.trend or _trend_label(out.metrics),
            )
    return out
