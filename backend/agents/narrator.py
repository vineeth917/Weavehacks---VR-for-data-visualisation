"""Narrator agent — session wrap-up from accumulated Redis state.

Rolls up dataset profile, EDA findings, preprocessor pipeline, trainer run,
and evals into a brief spoken summary + structured Report frame. Uses a
deterministic builder when session artifacts exist (no LLM hallucination);
falls back to the LLM only when state is thin.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

import weave
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from backend import config
from backend.agents.context import OrchestratorContext
from backend.agents.dataset_versions import get_v0
from backend.agents.parse import parse_json_object
from backend.tools import redis_state
from backend.tools.profiling import profile_dataset as _profile_dataset_impl

log = logging.getLogger("hololab.agents.narrator")


# ---------------------------------------------------------------------------
# Session-state rollup (pure Python, traced)
# ---------------------------------------------------------------------------

def _profile_summary(prof: dict[str, Any] | None) -> dict[str, Any]:
    if not prof:
        return {}
    cols = prof.get("columns", [])
    by_flag: dict[str, list[str]] = {}
    for c in cols:
        for f in (c.get("flags") or []):
            by_flag.setdefault(f, []).append(c.get("name", "?"))
    return {
        "dataset_name": prof.get("dataset_name"),
        "n_rows": prof.get("n_rows"),
        "n_cols": prof.get("n_cols"),
        "flagged_columns": {k: v for k, v in by_flag.items() if v},
        "top_correlations": (prof.get("top_correlations") or [])[:3],
    }


def _findings_summary(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for f in findings:
        key = (f.get("column", ""), f.get("flag", ""))
        seen[key] = f
    return list(seen.values())


def _training_summary(run_cache: dict[str, Any] | None) -> dict[str, Any]:
    if not run_cache:
        return {}
    summary = run_cache.get("summary") or {}
    return {
        "run_id": run_cache.get("run_id"),
        "source": run_cache.get("source"),
        "model_name": run_cache.get("model_name") or (run_cache.get("config") or {}).get("model"),
        "problem_type": run_cache.get("problem_type"),
        "target_column": run_cache.get("target_column"),
        "dataset_name": run_cache.get("dataset_name"),
        "version": run_cache.get("version"),
        "n_epochs": run_cache.get("n_epochs"),
        "final_train_loss": run_cache.get("final_train_loss") or summary.get("final_train_loss"),
        "final_val_loss": run_cache.get("final_val_loss") or summary.get("final_val_loss"),
        "trend": run_cache.get("trend") or summary.get("trend"),
        "wandb_url": run_cache.get("wandb_url"),
        "n_metrics": len(run_cache.get("metrics") or []),
    }


def _dataset_meta(sid: str) -> dict[str, Any]:
    return redis_state.jget(f"session:{sid}:dataset_meta") or {}


@weave.op()
def collect_session_state(sid: str) -> dict[str, Any]:
    """Roll up everything we know about a session, from Redis only."""
    prof = redis_state.get_profile(sid)
    findings = redis_state.get_findings(sid)
    trainer_run = redis_state.get_scratch(sid, "trainer_run")
    monitor_run = redis_state.get_scratch(sid, "training_run")
    problem_type = redis_state.get_scratch(sid, "problem_type") or {}
    preprocessor = redis_state.get_scratch(sid, "preprocessor") or {}
    evals = redis_state.get_scratch(sid, "evals") or {}
    meta = _dataset_meta(sid)
    memory = redis_state.get_memory(sid, n=20)

    initial_prof: dict[str, Any] = {}
    v0 = get_v0(sid)
    if v0 is not None and not v0.empty:
        initial_prof = _profile_summary(_profile_dataset_impl(v0))
        initial_prof["n_rows"] = len(v0)

    training = _training_summary(trainer_run) or _training_summary(monitor_run)

    return {
        "session_id": sid,
        "dataset": _profile_summary(prof),
        "initial_dataset": initial_prof,
        "dataset_meta": {
            "name": meta.get("name"),
            "current_version": meta.get("current_version"),
            "v0_rows": meta.get("v0_rows"),
            "last_changes": meta.get("last_changes") or [],
        },
        "findings": _findings_summary(findings),
        "problem_type": problem_type,
        "preprocessor": {
            "split": preprocessor.get("split"),
            "pipeline_log": preprocessor.get("pipeline_log") or [],
        },
        "training": training,
        "monitor": _training_summary(monitor_run) if monitor_run else {},
        "evals": evals,
        "memory_sample": [
            {"role": m.get("role"), "text": (m.get("text") or m.get("action") or "")[:120]}
            for m in memory[-6:]
        ],
        "has_dataset": bool(prof),
        "has_findings": bool(findings),
        "has_training": bool(trainer_run or monitor_run),
        "has_evals": bool(evals),
        "has_preprocessor": bool(preprocessor.get("pipeline_log") or preprocessor.get("split")),
    }


def _flag_list(flags: dict[str, list[str]], *kinds: str) -> str:
    cols: list[str] = []
    for k in kinds:
        cols.extend(flags.get(k) or [])
    return ", ".join(dict.fromkeys(cols)) if cols else ""


def _pipeline_sentences(pipeline_log: list[dict[str, Any]]) -> list[str]:
    sentences: list[str] = []
    for entry in pipeline_log:
        for change in entry.get("changes") or []:
            if change and change not in sentences:
                sentences.append(str(change))
    return sentences


def build_deterministic_narrative(state: dict[str, Any]) -> NarratorOutput | None:
    """Build a brief wrap-up from real session artifacts. Returns None if too thin."""
    if not state.get("has_dataset"):
        return None

    ds = state.get("dataset") or {}
    initial = state.get("initial_dataset") or {}
    meta = state.get("dataset_meta") or {}
    pt = state.get("problem_type") or {}
    prep = state.get("preprocessor") or {}
    trainer = state.get("training") or {}
    evals = state.get("evals") or {}
    findings = state.get("findings") or []

    name = ds.get("dataset_name") or meta.get("name") or "the dataset"
    v0_rows = initial.get("n_rows") or meta.get("v0_rows")
    cur_rows = ds.get("n_rows")
    n_cols = ds.get("n_cols") or initial.get("n_cols")

    kind = pt.get("problem_type") or evals.get("problem_type") or trainer.get("problem_type") or "unknown"
    target = pt.get("target_column") or evals.get("target_column") or trainer.get("target_column") or "?"

    init_flags = initial.get("flagged_columns") or {}
    skewed = _flag_list(init_flags, "right_skewed", "left_skewed")
    outliers = _flag_list(init_flags, "outliers")
    missing = _flag_list(init_flags, "heavy_missing")

    pipeline = _pipeline_sentences(prep.get("pipeline_log") or [])
    clean_bits = [c for c in pipeline if any(
        k in c.lower() for k in ("imputed", "dropped", "duplicate", "log-transform", "clipped")
    )]
    ready_bits = [c for c in pipeline if any(
        k in c.lower() for k in ("split", "encoded", "scaled", "oversampled", "balanced")
    )]

    split = prep.get("split") or {}
    sections: list[NarratorSection] = []

    ds_body = (
        f"{name}: {v0_rows or '?'} rows × {n_cols or '?'} cols at load"
        f" → v{meta.get('current_version', '?')} now {cur_rows or '?'} rows."
    )
    if kind != "unknown":
        ds_body += f" Problem: {kind} on '{target}'."
    sections.append(NarratorSection(title="Dataset & problem", body=ds_body))

    issues: list[str] = []
    if skewed:
        issues.append(f"skewed [{skewed}]")
    if outliers:
        issues.append(f"outliers [{outliers}]")
    if missing:
        issues.append(f"heavy missing [{missing}]")
    if findings:
        notes = "; ".join(
            f"{f.get('column')}: {f.get('flag')}" for f in findings[:4] if f.get("column")
        )
        if notes:
            issues.append(f"EDA flagged {notes}")

    clean_body = "Initial issues: " + ("; ".join(issues) if issues else "none flagged.")
    if clean_bits:
        clean_body += " Cleaned: " + " — ".join(clean_bits[:4]) + "."
    sections.append(NarratorSection(title="Cleaning", body=clean_body))

    if ready_bits or split:
        ready_body = ""
        if ready_bits:
            ready_body = "Make-ready: " + " — ".join(ready_bits[:4]) + "."
        if split:
            ready_body += (
                f" Stored split train={split.get('train_rows')} "
                f"val={split.get('val_rows')} at v{split.get('version')}."
            )
        sections.append(NarratorSection(title="Ready for training", body=ready_body.strip()))

    if trainer:
        tr_body = (
            f"Trained {trainer.get('model_name') or 'model'} for "
            f"{trainer.get('n_epochs') or '?'} epochs on preprocessor train split "
            f"(v{trainer.get('version', '?')}) — "
            f"final train loss {trainer.get('final_train_loss')}, "
            f"val loss {trainer.get('final_val_loss')} ({trainer.get('trend') or 'done'})."
        )
        if trainer.get("wandb_url"):
            tr_body += f" W&B: {trainer['wandb_url']}."
        sections.append(NarratorSection(title="Training", body=tr_body))

    if evals:
        if evals.get("problem_type") == "regression" or evals.get("rmse") is not None:
            ev_body = (
                f"Evals on held-out split: RMSE {evals.get('rmse')}, "
                f"MAE {evals.get('mae')}, R² {evals.get('r2')}."
            )
        else:
            ev_body = (
                f"Evals on held-out split: accuracy {evals.get('accuracy')}"
            )
            if evals.get("worst_class") is not None:
                ev_body += (
                    f", weakest recall class '{evals.get('worst_class')}' "
                    f"at {evals.get('worst_recall')}."
                )
            ev_body += " See confusion matrix."
        sections.append(NarratorSection(title="Evals", body=ev_body))

    rec = "Review weakest class recall and try more features or regularization."
    if kind == "regression":
        rec = "Inspect predicted-vs-actual scatter for systematic bias."
    if not trainer:
        rec = "Run make-ready then train the model to complete the loop."
    elif not evals:
        rec = "Run evals on the cleaned split to quantify test performance."
    sections.append(NarratorSection(title="Recommendation", body=rec))

    speech_parts = [f"We worked on {name}"]
    if kind != "unknown":
        speech_parts.append(f"a {kind} task on {target}")
    if v0_rows and cur_rows:
        speech_parts.append(f"from {v0_rows} rows down to {cur_rows} after cleaning")
    if split:
        speech_parts.append(
            f"split {split.get('train_rows')}/{split.get('val_rows')} for training"
        )
    if trainer:
        speech_parts.append(
            f"trained {trainer.get('n_epochs')} epochs (val loss {trainer.get('final_val_loss')})"
        )
    if evals.get("accuracy") is not None:
        speech_parts.append(f"test accuracy {float(evals['accuracy']):.2f}")
    elif evals.get("rmse") is not None:
        speech_parts.append(f"test RMSE {float(evals['rmse']):.2f}")

    speech = ", ".join(speech_parts) + "."

    verdict: Verdict = "mixed"
    if not trainer and not evals:
        verdict = "data_issues" if issues else "insufficient_data"
    elif trainer and evals:
        acc = evals.get("accuracy")
        if acc is not None and float(acc) >= 0.75:
            verdict = "healthy"
        elif evals.get("r2") is not None and float(evals["r2"]) >= 0.8:
            verdict = "healthy"
        else:
            verdict = "mixed"
    elif trainer:
        verdict = "mixed"

    return NarratorOutput(speech=speech[:400], verdict=verdict, sections=sections)


# ---------------------------------------------------------------------------
# Agent-facing tool
# ---------------------------------------------------------------------------

@function_tool
async def get_session_state_tool(ctx: RunContextWrapper[OrchestratorContext]) -> dict[str, Any]:
    """Return rolled-up session state for narration."""
    return collect_session_state(ctx.context.session_id)


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------

Verdict = Literal[
    "healthy", "data_issues", "overfitting", "underfitting", "leakage",
    "mixed", "insufficient_data",
]


class NarratorSection(BaseModel):
    title: str
    body: str


class NarratorOutput(BaseModel):
    speech: str
    verdict: str
    sections: list[NarratorSection] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Model factories (LLM fallback)
# ---------------------------------------------------------------------------

def _coreweave_model() -> OpenAIChatCompletionsModel:
    client = AsyncOpenAI(
        base_url=config.WANDB_INFERENCE_BASE,
        api_key=config.WANDB_API_KEY,
        default_headers={"User-Agent": "hololab-backend/0.0.2"},
    )
    return OpenAIChatCompletionsModel(model=config.REASONING_MODEL, openai_client=client)


def _openai_fallback_model() -> OpenAIChatCompletionsModel:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; cannot fall back")
    return OpenAIChatCompletionsModel(
        model=config.FALLBACK_MODEL,
        openai_client=AsyncOpenAI(api_key=config.OPENAI_API_KEY),
    )


_SYSTEM_PROMPT = """\
You are the narrator agent. Call `get_session_state_tool` once, then emit JSON
with speech, verdict, and sections citing ONLY numbers from the tool output.
Prefer trainer_run and evals scratch over stale v0 profile when they disagree.
"""


def _make_agent(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    return Agent[OrchestratorContext](
        name="narrator",
        handoff_description="Wrap-up / summary / recap of the session.",
        instructions=_SYSTEM_PROMPT,
        model=model,
        model_settings=ModelSettings(temperature=0.2, parallel_tool_calls=False),
        tools=[get_session_state_tool],
    )


_primary: Agent[OrchestratorContext] | None = None
_fallback: Agent[OrchestratorContext] | None = None


def get_agent() -> Agent[OrchestratorContext]:
    global _primary
    if _primary is None:
        _primary = _make_agent(_coreweave_model())
    return _primary


def get_fallback_agent() -> Agent[OrchestratorContext]:
    global _fallback
    if _fallback is None:
        _fallback = _make_agent(_openai_fallback_model())
    return _fallback


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@weave.op()
async def run_narrator(sid: str, *, text: str | None = None) -> NarratorOutput:
    """Generate a wrap-up narrative for this session."""
    state = collect_session_state(sid)
    det = build_deterministic_narrative(state)
    if det is not None and (
        state.get("has_preprocessor") or state.get("has_training") or state.get("has_evals")
    ):
        log.info("narrator deterministic sid=%s sections=%d", sid, len(det.sections))
        return det

    ctx = OrchestratorContext(session_id=sid)
    user_text = text or "Summarise this session."
    for label, getter in (("primary", get_agent), ("fallback", get_fallback_agent)):
        try:
            agent = getter()
            result = await Runner.run(agent, user_text, context=ctx, max_turns=6)
            raw = result.final_output if isinstance(result.final_output, str) \
                else str(result.final_output)
            return _parse_output(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("narrator %s agent failed: %s", label, e)
            continue

    if det is not None:
        return det
    return NarratorOutput(
        speech="I couldn't put together a wrap-up for this session.",
        verdict="insufficient_data",
        sections=[],
    )


def _parse_output(raw: str) -> NarratorOutput:
    data = parse_json_object(raw or "")
    if data is None:
        return NarratorOutput(
            speech=(raw or "I couldn't structure a wrap-up.")[:300],
            verdict="insufficient_data", sections=[],
        )
    try:
        return NarratorOutput.model_validate(data)
    except Exception as e:  # noqa: BLE001
        log.warning("narrator validation failed: %s — degrading", e)
        return NarratorOutput(
            speech=str(data.get("speech") or raw)[:300],
            verdict=str(data.get("verdict") or "mixed"),
            sections=[
                NarratorSection(title=str(s.get("title", "?")),
                                body=str(s.get("body", "")))
                for s in (data.get("sections") or [])
                if isinstance(s, dict)
            ],
        )
