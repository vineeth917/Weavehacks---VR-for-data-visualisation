"""Evals agent — real sklearn eval on the session's cleaned dataset (ENABLE_EVALS=1).

Trains LogisticRegression / Ridge on the current dataset version (v1 if present),
reports TEST-set metrics only. Speech cites real numbers; gpt-4o-mini optional
for handoff graph only — primary path is deterministic via run_for_query.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any, Literal

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import weave
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from backend import config
from backend.agents.context import OrchestratorContext
from backend.agents.dataset_versions import current_version, get_working
from backend.agents.parse import parse_json_object
from backend.agents.problem_type import _pick_target_column
from backend.contracts import Panel
from backend.tools import redis_state

log = logging.getLogger("hololab.agents.evals")

ProblemType = Literal["classification", "regression", "unknown"]
_FIGSIZE = (3.6, 2.6)


def _fig_to_b64(fig: plt.Figure, *, max_kb: int = 80) -> str:
    for dpi in (110, 90, 75, 60, 48):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.05)
        data = buf.getvalue()
        if len(data) <= max_kb * 1024:
            break
    plt.close(fig)
    return base64.b64encode(data).decode("ascii")


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


def _prepare_features(df: pd.DataFrame, target_col: str) -> tuple[pd.DataFrame, pd.Series]:
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
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X.astype(float))
    return pd.DataFrame(X_scaled, columns=[str(c) for c in X.columns], index=X.index), y


def _render_confusion_panel(cm: np.ndarray, labels: list[str]) -> Panel:
    fig, ax = plt.subplots(figsize=_FIGSIZE)
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(labels)), labels, fontsize=7, rotation=45)
    ax.set_yticks(range(len(labels)), labels, fontsize=7)
    ax.set_xlabel("predicted", fontsize=8)
    ax.set_ylabel("actual", fontsize=8)
    ax.set_title("Confusion matrix (test)", fontsize=9)
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, str(int(cm[i, j])), ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, fraction=0.046)
    b64 = _fig_to_b64(fig)
    return Panel(
        id="evals_confusion",
        kind="corr",
        title="Confusion matrix (test)",
        column=None,
        image_b64=b64,
        position_hint="center",
        flags=["evals"],
    )


def _render_regression_panel(y_test: np.ndarray, y_pred: np.ndarray) -> Panel:
    fig, ax = plt.subplots(figsize=_FIGSIZE)
    ax.scatter(y_test, y_pred, alpha=0.55, s=12, color="#3cb371", edgecolors="none")
    lo = float(min(y_test.min(), y_pred.min()))
    hi = float(max(y_test.max(), y_pred.max()))
    ax.plot([lo, hi], [lo, hi], "--", color="#888", linewidth=1)
    ax.set_xlabel("actual", fontsize=8)
    ax.set_ylabel("predicted", fontsize=8)
    ax.set_title("Predicted vs actual (test)", fontsize=9)
    ax.tick_params(labelsize=7)
    b64 = _fig_to_b64(fig)
    return Panel(
        id="evals_scatter",
        kind="corr",
        title="Predicted vs actual (test)",
        column=None,
        image_b64=b64,
        position_hint="center",
        flags=["evals"],
    )


def _speech_from_metrics(problem_type: str, metrics: dict[str, Any]) -> str:
    if problem_type == "classification":
        acc = metrics.get("accuracy", 0.0)
        worst = metrics.get("worst_class")
        worst_rec = metrics.get("worst_recall", 0.0)
        if worst:
            return (
                f"Test accuracy {acc:.2f} — recall on class '{worst}' is "
                f"{worst_rec:.2f}; check the confusion matrix for where it slips."
            )
        return f"Test accuracy {acc:.2f} on the held-out split."
    rmse = metrics.get("rmse", 0.0)
    r2 = metrics.get("r2", 0.0)
    return f"Test RMSE {rmse:.3f}, R² {r2:.3f} on the held-out split."


@weave.op()
def op_run_evals(sid: str, dataset_name: str, df: pd.DataFrame | None) -> dict[str, Any]:
    """Train a real sklearn model on the session df; return TEST metrics + panel data."""
    version = 0
    if config.ENABLE_PREPROCESSOR:
        w = get_working(sid)
        if w is not None:
            version, dataset_name, df = w[0], w[1], w[2]
        else:
            version = current_version(sid) or 0
    else:
        version = 0

    if df is None or df.empty:
        return {"error": "no dataset loaded", "version": version}

    target_col = _pick_target_column(df)
    problem_type = _resolve_problem_type(sid, df, target_col)

    if problem_type == "unknown":
        return {"error": f"could not determine problem type for target '{target_col}'",
                "version": version, "target_column": target_col}

    try:
        X, y = _prepare_features(df, target_col)
    except Exception as e:  # noqa: BLE001
        return {"error": f"feature prep failed: {e}", "version": version}

    if len(X) < 8:
        return {"error": f"not enough rows after prep ({len(X)})", "version": version}

    stratify = y if problem_type == "classification" and y.nunique() > 1 else None
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42,
            stratify=stratify,
        )
    except ValueError:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42,
        )

    if problem_type == "classification":
        model = LogisticRegression(max_iter=1000)
    else:
        model = Ridge()
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    metrics: dict[str, Any] = {
        "problem_type": problem_type,
        "target_column": target_col,
        "version": version,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "dataset_name": dataset_name,
    }

    panel: Panel | None = None
    if problem_type == "classification":
        labels = sorted(set(list(y_test) + list(y_pred)), key=str)
        cm = confusion_matrix(y_test, y_pred, labels=labels)
        acc = float(accuracy_score(y_test, y_pred))
        report = classification_report(y_test, y_pred, labels=labels,
                                       output_dict=True, zero_division=0)
        worst_class, worst_rec = None, 1.0
        for lbl in labels:
            key = str(lbl)
            if key in report and isinstance(report[key], dict):
                rec = float(report[key].get("recall", 0.0))
                if rec < worst_rec:
                    worst_rec, worst_class = rec, key
        metrics.update({
            "accuracy": acc,
            "confusion_matrix": cm.tolist(),
            "labels": [str(x) for x in labels],
            "classification_report": report,
            "worst_class": worst_class,
            "worst_recall": worst_rec,
        })
        panel = _render_confusion_panel(cm, [str(x) for x in labels])
    else:
        y_test_arr = np.asarray(y_test, dtype=float)
        y_pred_arr = np.asarray(y_pred, dtype=float)
        rmse = float(np.sqrt(mean_squared_error(y_test_arr, y_pred_arr)))
        mae = float(mean_absolute_error(y_test_arr, y_pred_arr))
        r2 = float(r2_score(y_test_arr, y_pred_arr))
        metrics.update({"rmse": rmse, "mae": mae, "r2": r2})
        panel = _render_regression_panel(y_test_arr, y_pred_arr)

    redis_state.set_scratch(sid, "evals", metrics)
    return {"ok": True, "metrics": metrics, "panel": panel.model_dump()}


# ---------------------------------------------------------------------------
# Agent tool + output
# ---------------------------------------------------------------------------

class EvalsOutput(BaseModel):
    speech: str
    problem_type: ProblemType = "unknown"
    target_column: str | None = None
    version: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    panels: list[Panel] = Field(default_factory=list)


_EMPTY = EvalsOutput(
    speech="I couldn't run evals — load a dataset and try again.",
    problem_type="unknown",
)


@function_tool
async def run_evals_tool(ctx: RunContextWrapper[OrchestratorContext]) -> dict[str, Any]:
    """Train a real sklearn model on the session dataset and return TEST metrics."""
    return op_run_evals(ctx.context.session_id, ctx.context.dataset_name, ctx.context.df)


def _openai_model() -> OpenAIChatCompletionsModel:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; evals agent requires OpenAI")
    return OpenAIChatCompletionsModel(
        model=config.FALLBACK_MODEL,
        openai_client=AsyncOpenAI(api_key=config.OPENAI_API_KEY),
    )


_SYSTEM_PROMPT = """\
You are the evals agent. Call `run_evals_tool` ONCE first — it trains a real
sklearn model on the session data and returns TEST metrics.

Then emit JSON inside ```json ... ``` using ONLY numbers from the tool:

  {
    "speech": "<one sentence citing real test accuracy or RMSE/R²>",
    "problem_type": "classification" | "regression",
    "target_column": "<from tool>",
    "version": <int from tool>,
    "metrics": { ...subset of tool metrics... }
  }

If the tool returns error, speech must say "couldn't run evals because <reason>".
Do NOT invent metrics.
"""


def _make_agent(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    return Agent[OrchestratorContext](
        name="evals",
        handoff_description=(
            "Use when the user wants model evaluation results on the cleaned "
            "dataset: run evals, confusion matrix, accuracy, precision/recall, "
            "RMSE, MAE, R², or asks how the model performed."
        ),
        instructions=_SYSTEM_PROMPT,
        model=model,
        model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
        tools=[run_evals_tool],
    )


_agent: Agent[OrchestratorContext] | None = None


def get_agent() -> Agent[OrchestratorContext]:
    global _agent
    if _agent is None:
        _agent = _make_agent(_openai_model())
    return _agent


def get_fallback_agent() -> Agent[OrchestratorContext]:
    return get_agent()


def _output_from_result(result: dict[str, Any]) -> EvalsOutput:
    if result.get("error"):
        return EvalsOutput(
            speech=f"couldn't run evals because {result['error']}",
            version=int(result.get("version") or 0),
            target_column=result.get("target_column"),
        )
    metrics = result.get("metrics") or {}
    panel_d = result.get("panel")
    panels = [Panel.model_validate(panel_d)] if panel_d else []
    pt = str(metrics.get("problem_type") or "unknown")
    return EvalsOutput(
        speech=_speech_from_metrics(pt, metrics),
        problem_type=pt if pt in ("classification", "regression") else "unknown",
        target_column=metrics.get("target_column"),
        version=int(metrics.get("version") or 0),
        metrics=metrics,
        panels=panels,
    )


@weave.op()
async def run_for_query(
    sid: str, text: str, df: pd.DataFrame | None, dataset_name: str,
) -> EvalsOutput:
    """Deterministic eval path — real sklearn, no replay."""
    try:
        result = op_run_evals(sid, dataset_name, df)
        return _output_from_result(result)
    except Exception as e:  # noqa: BLE001
        log.warning("evals run failed: %s", e)
        return EvalsOutput(speech=f"couldn't run evals because {e}")


async def _run(text: str, ctx: OrchestratorContext) -> EvalsOutput:
    try:
        agent = get_agent()
        result = await Runner.run(agent, text, context=ctx, max_turns=6)
        raw = result.final_output if isinstance(result.final_output, str) \
            else str(result.final_output)
        return _parse_output(raw, ctx)
    except Exception as e:  # noqa: BLE001
        log.warning("evals agent failed: %s", e)
        return await run_for_query(ctx.session_id, text, ctx.df, ctx.dataset_name)


def _parse_output(raw: str, ctx: OrchestratorContext) -> EvalsOutput:
    data = parse_json_object(raw)
    if data is None:
        return _EMPTY
    try:
        out = EvalsOutput.model_validate(data)
    except ValidationError:
        out = EvalsOutput(
            speech=str(data.get("speech") or raw)[:300],
            problem_type=data.get("problem_type") or "unknown",
            target_column=data.get("target_column"),
            version=int(data.get("version") or 0),
            metrics=data.get("metrics") or {},
        )
    if not out.panels:
        cached = op_run_evals(ctx.session_id, ctx.dataset_name, ctx.df)
        if cached.get("panel"):
            out.panels = [Panel.model_validate(cached["panel"])]
        if not out.metrics and cached.get("metrics"):
            out.metrics = cached["metrics"]
    if not out.speech or out.speech == _EMPTY.speech:
        if out.metrics:
            out.speech = _speech_from_metrics(out.problem_type, out.metrics)
    return out


def parse_evals_output(raw: str, ctx: OrchestratorContext) -> EvalsOutput:
    return _parse_output(raw, ctx)
