"""Preprocessor agent — versioned data cleanup / encoding (ENABLE_PREPROCESSOR=1).

Uses OpenAI gpt-4o-mini. Prompt-and-parse only — NO output_type=.

One transform pass per query; re-profile once after applying the plan.
Read-only readiness checks do not mutate data.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Literal

import numpy as np
import pandas as pd
import weave
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from backend import config
from backend.agents.context import OrchestratorContext
from backend.agents.dataset_versions import (
    current_version,
    ensure_baseline,
    get_v0,
    get_working,
    save_new_version,
)
from backend.agents.eda import PanelSpec
from backend.agents.parse import parse_json_object
from backend.tools import redis_state
from backend.tools.profiling import profile_dataset as _profile_dataset_impl

log = logging.getLogger("hololab.agents.preprocessor")

_TARGET_NAMES = frozenset({"target", "label", "y", "survived", "class", "category", "mpg"})
_MAX_SKEW = 1.0


# ---------------------------------------------------------------------------
# Transform ops (pure pandas / sklearn, @weave.op)
# ---------------------------------------------------------------------------

def _is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def _target_column(df: pd.DataFrame) -> str:
    for col in df.columns:
        if str(col).lower() in _TARGET_NAMES:
            return str(col)
    return str(df.columns[-1])


@weave.op()
def op_drop_nulls(df: pd.DataFrame, strategy: str = "drop_rows") -> tuple[pd.DataFrame, str]:
    before = len(df)
    if strategy == "impute_median":
        out = df.copy()
        for col in out.columns:
            if _is_numeric(out[col]):
                out[col] = pd.to_numeric(out[col], errors="coerce")
                med = out[col].median()
                out[col] = out[col].fillna(med)
            else:
                mode = out[col].mode()
                out[col] = out[col].fillna(mode.iloc[0] if len(mode) else "")
        filled = int(out.isna().sum().sum())
        return out, (
            f"imputed nulls with median (numeric) and mode (categorical) "
            f"— {before} rows kept, {filled} cells filled"
        )
    out = df.dropna().reset_index(drop=True)
    dropped = before - len(out)
    return out, f"dropped {dropped} rows with nulls ({before} → {len(out)})"


@weave.op()
def op_drop_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    before = len(df)
    out = df.drop_duplicates().reset_index(drop=True)
    dropped = before - len(out)
    return out, f"dropped {dropped} duplicate rows ({before} → {len(out)})"


@weave.op()
def op_clip_outliers(df: pd.DataFrame, columns: list[str] | None = None) -> tuple[pd.DataFrame, str]:
    out = df.copy()
    cols = columns or [c for c in out.columns if _is_numeric(out[c])]
    clipped = 0
    for col in cols:
        if col not in out.columns or not _is_numeric(out[col]):
            continue
        s = pd.to_numeric(out[col], errors="coerce")
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        n = int(((s < lo) | (s > hi)).sum())
        out[col] = s.clip(lo, hi)
        clipped += n
    return out, f"clipped {clipped} IQR outlier values across {len(cols)} column(s)"


@weave.op()
def op_log_transform(df: pd.DataFrame, column: str) -> tuple[pd.DataFrame, str]:
    if column not in df.columns:
        raise ValueError(f"column {column!r} not found")
    out = df.copy()
    s = pd.to_numeric(out[column], errors="coerce")
    skew_before = float(s.dropna().skew()) if len(s.dropna()) >= 3 else 0.0
    out[column] = np.log1p(s.clip(lower=0))
    skew_after = float(out[column].dropna().skew()) if len(out[column].dropna()) >= 3 else 0.0
    return out, (
        f"log-transformed {column} (skew {skew_before:.2f} → {skew_after:.2f})"
    )


@weave.op()
def op_scale(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    out = df.copy()
    target = _target_column(out)
    num_cols = [c for c in out.columns if _is_numeric(out[c]) and c != target]
    if not num_cols:
        return out, "no numeric columns to scale"
    scaler = StandardScaler()
    out[num_cols] = scaler.fit_transform(out[num_cols].astype(float))
    return out, f"standard-scaled {len(num_cols)} numeric column(s)"


@weave.op()
def op_one_hot_encode(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    target = _target_column(df)
    cat_cols: list[str] = []
    for c in df.columns:
        if c == target:
            continue
        n_u = int(df[c].nunique(dropna=True))
        if n_u > 20:
            continue
        if not _is_numeric(df[c]) or isinstance(df[c].dtype, object):
            cat_cols.append(c)
        elif pd.api.types.is_integer_dtype(df[c]) and n_u <= 20:
            cat_cols.append(c)
    if not cat_cols:
        return df.copy(), "no low-cardinality categoricals to encode"
    out = pd.get_dummies(df, columns=cat_cols, drop_first=True)
    return out, f"one-hot encoded {len(cat_cols)} categorical column(s)"


@weave.op()
def op_check_imbalance(df: pd.DataFrame, target: str | None = None) -> dict[str, Any]:
    col = target or _target_column(df)
    if col not in df.columns:
        return {"error": f"target column {col!r} not found"}
    vc = df[col].value_counts(dropna=True)
    total = int(vc.sum())
    ratios = {str(k): round(float(v) / total, 3) for k, v in vc.items()}
    minority = float(vc.min() / total) if total else 1.0
    imbalanced = minority < 0.35 and len(vc) > 1
    return {
        "target": col,
        "counts": {str(k): int(v) for k, v in vc.items()},
        "ratios": ratios,
        "imbalanced": imbalanced,
        "minority_frac": round(minority, 3),
    }


@weave.op()
def op_resample_balance(df: pd.DataFrame, target: str | None = None) -> tuple[pd.DataFrame, str]:
    col = target or _target_column(df)
    if col not in df.columns:
        raise ValueError(f"target column {col!r} not found")
    vc = df[col].value_counts()
    if len(vc) < 2:
        return df.copy(), f"only one class in {col}; no resampling needed"
    max_n = int(vc.max())
    parts = []
    for cls, _ in vc.items():
        subset = df[df[col] == cls]
        if len(subset) < max_n:
            extra = subset.sample(max_n - len(subset), replace=True, random_state=7)
            parts.append(pd.concat([subset, extra], ignore_index=True))
        else:
            parts.append(subset)
    out = pd.concat(parts, ignore_index=True).sample(frac=1, random_state=7).reset_index(drop=True)
    after = op_check_imbalance(out, col)
    ratios = after.get("ratios") or {}
    ratio_str = "/".join(f"{int(r*100)}" for r in ratios.values()) if ratios else "balanced"
    return out, f"oversampled {col} toward balance (~{ratio_str})"


def _df_to_blob(df: pd.DataFrame) -> dict[str, Any]:
    return {"columns": [str(c) for c in df.columns], "records": df.to_dict(orient="records")}


def _blob_to_df(blob: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(blob.get("records") or [], columns=blob.get("columns") or [])


def _save_train_val_split(
    sid: str,
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    target: str,
    version: int,
) -> None:
    scratch = dict(redis_state.get_scratch(sid, "preprocessor") or {})
    scratch["split"] = {
        "version": version,
        "target": target,
        "train_rows": len(train_df),
        "val_rows": len(val_df),
        "train": _df_to_blob(train_df),
        "val": _df_to_blob(val_df),
    }
    redis_state.set_scratch(sid, "preprocessor", scratch)


def get_train_val_split(sid: str, version: int | None = None) -> dict[str, Any] | None:
    """Return stored train/val split if it matches the current dataset version."""
    scratch = redis_state.get_scratch(sid, "preprocessor") or {}
    split = scratch.get("split")
    if not split:
        return None
    if version is not None and int(split.get("version", -1)) != int(version):
        return None
    return split


@weave.op()
def op_run_clean_pipeline(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Impute nulls, dedupe, log-transform skewed numerics, clip outliers."""
    changes: list[str] = []
    out, msg = op_drop_nulls(df, "impute_median")
    changes.append(msg)
    out, msg = op_drop_duplicates(out)
    changes.append(msg)
    prof = _profile_dataset_impl(out)
    for col in prof.get("columns") or []:
        flags = col.get("flags") or []
        if "right_skewed" in flags or "left_skewed" in flags:
            out, msg = op_log_transform(out, str(col["name"]))
            changes.append(msg)
    out, msg = op_clip_outliers(out)
    changes.append(msg)
    return out, changes


@weave.op()
def op_make_ready_for_training(sid: str, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Split → encode → resample train → scale features; persist train/val in scratch."""
    target = _target_column(df)
    if target not in df.columns:
        raise ValueError(f"target column {target!r} not found")

    y = df[target]
    stratify = y if y.nunique(dropna=True) > 1 and y.nunique(dropna=True) <= 20 else None
    try:
        train_df, val_df = train_test_split(
            df, test_size=0.25, random_state=42, stratify=stratify,
        )
    except ValueError:
        train_df, val_df = train_test_split(df, test_size=0.25, random_state=42)

    changes: list[str] = [
        f"split train={len(train_df)} val={len(val_df)} (75/25 stratified)",
    ]

    train_enc, enc_msg = op_one_hot_encode(train_df.reset_index(drop=True))
    val_enc, _ = op_one_hot_encode(val_df.reset_index(drop=True))
    val_enc = val_enc.reindex(columns=train_enc.columns, fill_value=0)
    changes.append(enc_msg)

    if op_check_imbalance(train_enc, target).get("imbalanced"):
        train_enc, bal_msg = op_resample_balance(train_enc, target)
        changes.append(bal_msg)
    else:
        changes.append(f"train split already balanced on {target}")

    feat_cols = [c for c in train_enc.columns if c != target]
    if not feat_cols:
        raise ValueError("no feature columns after encoding")

    scaler = StandardScaler()
    train_feats = scaler.fit_transform(train_enc[feat_cols].astype(float))
    val_feats = scaler.transform(val_enc[feat_cols].astype(float))
    train_out = pd.DataFrame(train_feats, columns=feat_cols)
    train_out[target] = train_enc[target].values
    val_out = pd.DataFrame(val_feats, columns=feat_cols)
    val_out[target] = val_enc[target].values
    changes.append(f"standard-scaled {len(feat_cols)} feature column(s) (target untouched)")

    ver = current_version(sid) or 0
    _save_train_val_split(sid, train_out, val_out, target, ver)

    combined = pd.concat([train_out, val_out], ignore_index=True)
    return combined, changes


@weave.op()
def op_apply_plan(
    sid: str,
    dataset_name: str,
    df: pd.DataFrame,
    plan: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply all transform steps in ONE pass, then re-profile once."""
    ensure_baseline(sid, dataset_name, df)
    ver_before = current_version(sid) or 0
    working = df.copy()
    changes: list[str] = []
    errors: list[str] = []

    for step in plan[:12]:
        op = str(step.get("op") or "")
        try:
            if op == "clean_data":
                working, step_changes = op_run_clean_pipeline(working)
                changes.extend(step_changes)
                continue
            if op == "make_ready":
                working, step_changes = op_make_ready_for_training(sid, working)
                changes.extend(step_changes)
                continue
            if op == "drop_nulls":
                working, msg = op_drop_nulls(working, str(step.get("strategy") or "drop_rows"))
            elif op == "drop_duplicates":
                working, msg = op_drop_duplicates(working)
            elif op == "clip_outliers":
                working, msg = op_clip_outliers(working, step.get("columns"))
            elif op == "log_transform":
                working, msg = op_log_transform(working, str(step.get("column")))
            elif op == "scale":
                working, msg = op_scale(working)
            elif op == "one_hot_encode":
                working, msg = op_one_hot_encode(working)
            elif op == "resample_balance":
                working, msg = op_resample_balance(working, step.get("target"))
            else:
                errors.append(f"unknown op {op!r}")
                continue
            changes.append(msg)
        except Exception as e:  # noqa: BLE001
            errors.append(f"couldn't apply {op}: {e}")
            log.warning("preprocess step %s failed: %s", op, e)

    if not changes and errors:
        return {
            "ok": False,
            "version_before": ver_before,
            "version_after": ver_before,
            "changes": [],
            "errors": errors,
            "profile": _profile_dataset_impl(working),
        }

    prof = _profile_dataset_impl(working)
    prof["dataset_name"] = dataset_name
    redis_state.set_profile(sid, prof)

    if changes:
        ver_after = save_new_version(sid, working, changes=changes)
        scratch = dict(redis_state.get_scratch(sid, "preprocessor") or {})
        if scratch.get("split") is not None:
            scratch["split"] = dict(scratch["split"])
            scratch["split"]["version"] = ver_after
        pipeline_log = list(scratch.get("pipeline_log") or [])
        pipeline_log.append({"version": ver_after, "changes": changes})
        scratch["pipeline_log"] = pipeline_log
        redis_state.set_scratch(sid, "preprocessor", scratch)
    else:
        ver_after = ver_before

    return {
        "ok": True,
        "version_before": ver_before,
        "version_after": ver_after,
        "changes": changes,
        "errors": errors,
        "profile": prof,
        "n_rows": len(working),
    }


@weave.op()
def op_check_readiness(sid: str, dataset_name: str, df: pd.DataFrame) -> dict[str, Any]:
    """Read-only readiness on the preprocessor train/val split (post encode + scale)."""
    ensure_baseline(sid, dataset_name, df)
    ver = current_version(sid) or 0
    issues: list[str] = []
    skewed: list[str] = []
    split = get_train_val_split(sid, ver)
    train_rows = val_rows = 0
    encoded = scaled = False
    imb: dict[str, Any] = {}

    if split is None:
        issues.append("no train/val split — run make ready for training first")
    else:
        train_df = _blob_to_df(split["train"])
        val_df = _blob_to_df(split["val"])
        target = str(split.get("target") or _target_column(df))
        train_rows = len(train_df)
        val_rows = len(val_df)

        if train_df.isna().any().any() or val_df.isna().any().any():
            issues.append("missing values remain in train or val split")
        if train_rows < 10:
            issues.append(f"train split too small ({train_rows} rows)")

        feat_cols = [c for c in train_df.columns if c != target]
        encoded = any("_" in c for c in feat_cols) or len(feat_cols) > len(df.columns) - 1
        if not encoded and len(feat_cols) <= 2:
            issues.append("categoricals not encoded yet")

        if feat_cols:
            means = [abs(float(train_df[c].mean())) for c in feat_cols[:12] if train_df[c].notna().any()]
            scaled = bool(means) and max(means) < 0.6
            if not scaled:
                issues.append("features not standard-scaled on train split")

        imb = op_check_imbalance(train_df, target)
        if imb.get("imbalanced"):
            issues.append(
                f"class imbalance on train {imb.get('target')}: {imb.get('ratios')}"
            )

    ready = len(issues) == 0
    return {
        "ready": ready,
        "verdict": "ready" if ready else "not ready",
        "issues": issues,
        "skewed_columns": skewed,
        "imbalance": imb,
        "encoded": encoded,
        "scaled_hint": scaled,
        "has_split": split is not None,
        "train_rows": train_rows,
        "val_rows": val_rows,
        "n_rows": len(df),
        "version": ver,
    }


def _panel_specs_from_profile(prof: dict[str, Any]) -> list[PanelSpec]:
    specs: list[PanelSpec] = []
    cols = prof.get("columns") or []
    heavy_miss = any(c.get("missing_pct", 0) >= 5 for c in cols)
    if heavy_miss:
        specs.append(PanelSpec(kind="missing"))
    for c in cols:
        flags = c.get("flags") or []
        if "right_skewed" in flags or "left_skewed" in flags or "outliers" in flags:
            kind = "kde" if c.get("unique_count", 0) > 30 else "histogram"
            specs.append(PanelSpec(
                column=c["name"], kind=kind, flags=flags,
                title=str(c["name"]).title(),
            ))
        if len(specs) >= 4:
            break
    if not specs:
        specs.append(PanelSpec(kind="corr"))
    return specs[:4]


# ---------------------------------------------------------------------------
# @function_tool wrappers
# ---------------------------------------------------------------------------

@function_tool
async def get_version_info_tool(ctx: RunContextWrapper[OrchestratorContext]) -> dict[str, Any]:
    """Current dataset version, shape, and compact profile summary."""
    sid = ctx.context.session_id
    name = ctx.context.dataset_name
    df = ctx.context.df
    if df is None or df.empty:
        return {"error": "no dataset loaded"}
    ensure_baseline(sid, name, df)
    prof = redis_state.get_profile(sid) or _profile_dataset_impl(df)
    compact = {
        "version": current_version(sid) or 0,
        "v0_rows": len(get_v0(sid) or df),
        "n_rows": len(df),
        "n_cols": len(df.columns),
        "columns": [
            {k: c[k] for k in ("name", "dtype", "missing_pct", "skew", "flags")
             if k in c}
            for c in (prof.get("columns") or [])[:12]
        ],
    }
    return compact


@function_tool
async def apply_preprocess_plan_tool(
    ctx: RunContextWrapper[OrchestratorContext],
    plan: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply a list of transform steps in ONE pass, then re-profile once.

    Allowed ops: drop_nulls, drop_duplicates, clip_outliers, log_transform,
    scale, one_hot_encode, resample_balance.
    Example plan:
      [{"op": "drop_nulls"}, {"op": "drop_duplicates"},
       {"op": "log_transform", "column": "price"}]
    """
    sid = ctx.context.session_id
    name = ctx.context.dataset_name
    df = ctx.context.df
    if df is None or df.empty:
        return {"error": "no dataset loaded"}
    result = op_apply_plan(sid, name, df, plan)
    if result.get("ok") and result.get("version_after", 0) > result.get("version_before", 0):
        w = get_working(sid)
        if w:
            ctx.context.df = w[2]
    return result


@function_tool
async def check_readiness_tool(ctx: RunContextWrapper[OrchestratorContext]) -> dict[str, Any]:
    """Read-only check: is the current dataset version ready for training?"""
    sid = ctx.context.session_id
    name = ctx.context.dataset_name
    df = ctx.context.df
    if df is None or df.empty:
        return {"error": "no dataset loaded", "ready": False, "verdict": "not ready"}
    return op_check_readiness(sid, name, df)


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------

Mode = Literal["transform", "readiness"]


class PreprocessorOutput(BaseModel):
    speech: str
    mode: Mode = "transform"
    ready: bool | None = None
    version_before: int = 0
    version_after: int | None = None
    changes: list[str] = Field(default_factory=list)
    panel_specs: list[PanelSpec] = Field(default_factory=list)


_EMPTY = PreprocessorOutput(
    speech="I couldn't preprocess this dataset — try a more specific request.",
    mode="transform",
)


# ---------------------------------------------------------------------------
# Model — gpt-4o-mini only
# ---------------------------------------------------------------------------

def _openai_model() -> OpenAIChatCompletionsModel:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; preprocessor requires OpenAI")
    return OpenAIChatCompletionsModel(
        model=config.FALLBACK_MODEL,
        openai_client=AsyncOpenAI(api_key=config.OPENAI_API_KEY),
    )


_SYSTEM_PROMPT = """\
You are the preprocessor agent inside HoloLab. You clean and prepare datasets
on versioned, non-destructive copies (v0 is always preserved).

CRITICAL RULES:
- Call `get_version_info_tool` FIRST to see the current version and flags.
- TRANSFORM queries (remove nulls, drop duplicates, log-transform skew, scale,
  encode, balance, "make ready for training"):
    • Build ONE `apply_preprocess_plan_tool` call with ALL steps for this turn.
    • Do NOT call apply_preprocess_plan_tool more than once per user query.
    • Do NOT loop trying to perfect the data — one pass only.
    • Pick log_transform for columns with right_skewed / left_skewed flags.
    • For "ready for training" cleanup: drop_nulls, drop_duplicates,
      log_transform on skewed numerics, clip_outliers, scale, one_hot_encode;
      call check_imbalance via resample_balance ONLY if imbalanced.
- READINESS queries ("is my data ready to train", "ready to train?"):
    • Call ONLY `check_readiness_tool` — do NOT mutate data.
    • Set mode="readiness" and ready=true/false from the tool.

After tool results, emit final JSON inside ```json ... ```:

  {
    "speech": "<one sentence, cite real numbers from tool output>",
    "mode": "transform" | "readiness",
    "ready": true | false | null,
    "version_before": <int>,
    "version_after": <int or same if readiness>,
    "changes": ["<bullet from apply result>", ...],
    "panel_specs": [
      {"column": "price", "kind": "kde", "flags": ["right_skewed"]},
      {"kind": "missing"}
    ]
  }

If apply_preprocess_plan_tool returns errors and no changes, speech must say
"couldn't apply X because Y" gracefully — never invent success.

panel_specs: up to 4 panels on the TRANSFORMED data — prefer kde/histogram on
skewed columns and missing for heavy_missing. Use kinds:
histogram, box, kde, corr, missing.
"""


def _make_agent(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    example = PreprocessorOutput(
        speech="Dropped 107 null rows and log-transformed price — skew fell from 3.7 to 0.4.",
        mode="transform",
        version_before=0,
        version_after=1,
        changes=["dropped 107 rows with nulls", "log-transformed price"],
        panel_specs=[PanelSpec(column="price", kind="kde")],
    )
    instructions = (
        _SYSTEM_PROMPT
        + "\nExample:\n```json\n"
        + json.dumps(example.model_dump(), indent=2)
        + "\n```\n"
    )
    return Agent[OrchestratorContext](
        name="preprocessor",
        handoff_description=(
            "Use this agent when the user wants to clean, transform, or prepare "
            "the dataset: remove nulls/duplicates/outliers, log-transform skewed "
            "columns, scale, encode categoricals, balance classes, or check if "
            "data is ready for training."
        ),
        instructions=instructions,
        model=model,
        model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
        tools=[get_version_info_tool, apply_preprocess_plan_tool, check_readiness_tool],
    )


_agent: Agent[OrchestratorContext] | None = None


def get_agent() -> Agent[OrchestratorContext]:
    global _agent
    if _agent is None:
        _agent = _make_agent(_openai_model())
    return _agent


def get_fallback_agent() -> Agent[OrchestratorContext]:
    return get_agent()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

@weave.op()
async def run_for_query(sid: str, text: str, df: pd.DataFrame, dataset_name: str) -> PreprocessorOutput:
    det = await run_deterministic(sid, text, df, dataset_name)
    if det is not None:
        return det

    ensure_baseline(sid, dataset_name, df)
    w = get_working(sid)
    if w:
        ver, name, working_df = w
        df = working_df
        dataset_name = name
    ctx = OrchestratorContext(session_id=sid, dataset_name=dataset_name, df=df)
    return await _run(text, ctx)


async def _run(text: str, ctx: OrchestratorContext) -> PreprocessorOutput:
    try:
        agent = get_agent()
        result = await Runner.run(agent, text, context=ctx, max_turns=8)
        raw = result.final_output if isinstance(result.final_output, str) \
            else str(result.final_output)
        out = _parse_output(raw, ctx)
        if out.mode == "transform" and not out.panel_specs:
            prof = redis_state.get_profile(ctx.session_id)
            if prof:
                out.panel_specs = _panel_specs_from_profile(prof)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("preprocessor agent failed: %s", e)
        return _EMPTY


def _parse_output(raw: str, ctx: OrchestratorContext) -> PreprocessorOutput:
    data = parse_json_object(raw)
    ver = current_version(ctx.session_id) or 0
    if data is None:
        return PreprocessorOutput(
            speech=(raw or _EMPTY.speech)[:300],
            version_before=ver,
            version_after=ver,
        )
    try:
        out = PreprocessorOutput.model_validate(data)
        if out.version_after is None:
            out.version_after = ver
        return out
    except ValidationError as e:
        log.warning("preprocessor validation failed: %s", e)
        return PreprocessorOutput(
            speech=str(data.get("speech") or raw)[:300],
            mode=data.get("mode") if data.get("mode") in ("transform", "readiness") else "transform",
            ready=data.get("ready"),
            version_before=int(data.get("version_before") or ver),
            version_after=int(data.get("version_after") or ver),
            changes=list(data.get("changes") or []),
            panel_specs=[
                PanelSpec.model_validate(p) for p in (data.get("panel_specs") or [])
                if isinstance(p, dict) and "kind" in p
            ][:4],
        )


def build_panel_specs_fallback(sid: str) -> list[PanelSpec]:
    prof = redis_state.get_profile(sid)
    return _panel_specs_from_profile(prof) if prof else []


def reset_preprocessor_scratch(sid: str) -> None:
    """Clear preprocessor split metadata (on load_dataset / reset)."""
    redis_state.set_scratch(sid, "preprocessor", {})


def reset_readiness_attempts(sid: str) -> None:
    """Backward-compatible alias."""
    reset_preprocessor_scratch(sid)


def _readiness_reasons(report: dict[str, Any]) -> str:
    parts: list[str] = []
    ver = report.get("version")
    if report.get("has_split"):
        parts.append(
            f"train={report.get('train_rows')} val={report.get('val_rows')} at v{ver}"
        )
    if report.get("encoded"):
        parts.append("categoricals encoded")
    if report.get("scaled_hint"):
        parts.append("features scaled")
    imb = report.get("imbalance") or {}
    if imb.get("target") and not imb.get("imbalanced"):
        parts.append(f"balanced train target '{imb.get('target')}'")
    issues = report.get("issues") or []
    if not issues:
        parts.append("no blocking issues")
    return "; ".join(parts[:5])


def _is_transform_ready_command(text: str) -> bool:
    """Imperative cleanup — must mutate data, not the read-only readiness check."""
    t = text.lower()
    return (
        "make the data ready" in t
        or "make it ready" in t
        or ("prepare" in t and "ready" in t and ("train" in t or "training" in t))
    )


def _is_readiness_query(text: str) -> bool:
    if _is_transform_ready_command(text):
        return False
    t = text.lower()
    return "ready" in t and ("train" in t or "training" in t or "data" in t)


def _is_make_ready_command(text: str) -> bool:
    t = text.lower()
    return (
        "make the data ready" in t
        or "make data ready" in t
        or "make ready for training" in t
        or ("make" in t and "ready" in t and ("train" in t or "training" in t))
    )


def _is_clean_query(text: str) -> bool:
    if _is_make_ready_command(text) or _is_readiness_query(text):
        return False
    t = text.lower()
    return any(k in t for k in (
        "clean", "remove null", "drop null", "impute", "duplicate",
        "log-transform", "log transform", "skew", "clip", "outlier",
        "preprocess",
    ))


def _output_from_apply(sid: str, result: dict[str, Any]) -> PreprocessorOutput:
    prof = redis_state.get_profile(sid) or {}
    changes = result.get("changes") or []
    speech = changes[0] if len(changes) == 1 else " — ".join(changes[:4])
    if not speech:
        speech = "No changes were applied."
    if not speech.endswith("."):
        speech += "."
    return PreprocessorOutput(
        speech=speech[:300],
        mode="transform",
        version_before=int(result.get("version_before") or 0),
        version_after=int(result.get("version_after") or 0),
        changes=changes,
        panel_specs=_panel_specs_from_profile(prof),
    )


def _output_from_readiness(sid: str, report: dict[str, Any]) -> PreprocessorOutput:
    ready = bool(report.get("ready"))
    issues = report.get("issues") or []
    ver = int(report.get("version") or current_version(sid) or 0)
    if ready:
        speech = f"Your data is ready to train: {_readiness_reasons(report)}."
    else:
        speech = "Not ready to train yet: " + "; ".join(issues[:3])
    return PreprocessorOutput(
        speech=speech[:400],
        mode="readiness",
        ready=ready,
        version_before=ver,
        version_after=ver,
        changes=[],
        panel_specs=_panel_specs_from_profile(redis_state.get_profile(sid) or {}),
    )


@weave.op()
async def run_deterministic(sid: str, text: str, df: pd.DataFrame,
                            dataset_name: str) -> PreprocessorOutput | None:
    """Keyword-routed deterministic path (no LLM). Returns None if not matched."""
    ensure_baseline(sid, dataset_name, df)
    w = get_working(sid)
    if w:
        df = w[2]
        dataset_name = w[1]

    if _is_readiness_query(text):
        return _output_from_readiness(sid, op_check_readiness(sid, dataset_name, df))

    if _is_make_ready_command(text):
        result = op_apply_plan(sid, dataset_name, df, [{"op": "make_ready"}])
        return _output_from_apply(sid, result)

    if _is_clean_query(text):
        result = op_apply_plan(sid, dataset_name, df, [{"op": "clean_data"}])
        return _output_from_apply(sid, result)

    return None
