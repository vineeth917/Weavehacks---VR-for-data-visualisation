"""Narrator agent — final TASKS_A piece.

Reads accumulated session state (dataset profile, EDA findings, last training
run + verdict) and NARRATES it. We do NOT recompute anything here — the
training-monitor already produced numerical evidence, the EDA agent already
produced findings; the narrator's only job is to synthesise a short, demo-
ready wrap-up.

Same prompt-and-parse pattern as the other agents (no `output_type=`; Qwen
short-circuits structured-output strict mode). All LLM/tool entry points are
@weave.op() wrapped.

Output: a `Report` frame (contracts.Report) sent over /ws, with a single
spoken summary + a handful of section bodies. There is no A2UI surface for
this — the Report frame itself drives the dashboard's narrative card if
Person C wants to render it. The dev UI already renders this frame as a
bullet list with a verdict heading.
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
from backend.agents.parse import parse_json_object
from backend.tools import redis_state

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
    """Dedupe findings by (column, flag) — keep the most recent note."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for f in findings:
        key = (f.get("column", ""), f.get("flag", ""))
        seen[key] = f
    return list(seen.values())


def _training_summary(run_cache: dict[str, Any] | None) -> dict[str, Any]:
    """Pick out just the numbers the narrator can quote."""
    if not run_cache:
        return {}
    return {
        "run_id": run_cache.get("run_id"),
        "source": run_cache.get("source"),
        "config": run_cache.get("config", {}),
        "summary": run_cache.get("summary", {}),
        "n_metrics": len(run_cache.get("metrics", [])),
    }


@weave.op()
def collect_session_state(sid: str) -> dict[str, Any]:
    """Roll up everything we know about a session, from Redis only.

    No DataFrames, no LLM calls, no math — just hand the LLM a compact
    `{dataset, findings, training, memory_sample}` blob to narrate.
    """
    prof = redis_state.get_profile(sid)
    findings = redis_state.get_findings(sid)
    run_cache = redis_state.get_scratch(sid, "training_run")
    memory = redis_state.get_memory(sid, n=20)

    return {
        "session_id": sid,
        "dataset": _profile_summary(prof),
        "findings": _findings_summary(findings),
        "training": _training_summary(run_cache),
        "memory_sample": [
            {"role": m.get("role"), "text": (m.get("text") or m.get("action") or "")[:120]}
            for m in memory[-6:]
        ],
        "has_dataset": bool(prof),
        "has_findings": bool(findings),
        "has_training": bool(run_cache),
    }


# ---------------------------------------------------------------------------
# Agent-facing tool — gives the LLM access via @function_tool
# (handoff-compatible signature; ctx provides session_id)
# ---------------------------------------------------------------------------

@function_tool
async def get_session_state_tool(ctx: RunContextWrapper[OrchestratorContext]
                                  ) -> dict[str, Any]:
    """Return everything known about this session (dataset profile, EDA
    findings, training run summary) rolled up into a single compact dict.

    You MUST call this exactly ONCE before producing your final JSON, even
    if the user query is short. Do not call it twice — there is no streaming
    state to re-poll.
    """
    return collect_session_state(ctx.context.session_id)


# ---------------------------------------------------------------------------
# Structured output (parsed, not enforced via output_type)
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
# Model factories
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


# ---------------------------------------------------------------------------
# Prompt + agent
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the narrator agent inside HoloLab. The user just finished an
exploratory session. Your job is to deliver a one-breath spoken summary plus a
short structured report.

CRITICAL RULES:
- You know NOTHING about the session until you call `get_session_state_tool`.
  Your FIRST action MUST be a tool call to `get_session_state_tool`. Do NOT
  invent column names, missing percentages, or loss values.
- After the tool returns, produce a JSON object wrapped in a single
  ```json ... ``` fence.
- ONLY narrate what's already in the state. The EDA agent and training-monitor
  did the analysis; you are the storyteller, not a re-analyzer.
- If the state shows no findings and no training run, your verdict should be
  "insufficient_data" and the speech should ask the user to interact more.

Final JSON shape (NarratorOutput):
  {
    "speech":   "<one or two sentences, <=40 words, suitable for TTS>",
    "verdict":  "healthy" | "data_issues" | "overfitting" | "underfitting"
                | "leakage" | "mixed" | "insufficient_data",
    "sections": [
      {"title": "Dataset",       "body": "<dataset summary citing n_rows × n_cols and any flagged columns>"},
      {"title": "EDA findings",  "body": "<bullet-style sentence listing the columns and flags>"},
      {"title": "Training",      "body": "<numeric verdict citing best_val_loss + final_val_loss + run_id>"},
      {"title": "Recommendation","body": "<one concrete next step the user should take>"}
    ]
  }

Sections should be present only if you have data for them:
  - omit "Dataset" if dataset_name is missing
  - omit "EDA findings" if findings list is empty
  - omit "Training" if no run cached
  - always include "Recommendation"

Style: factual, present tense, <=2 sentences per section. Quote real numbers
from the state object (not approximations). The dev UI renders each section
as a list item under the verdict.
"""


def _make_agent(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    return Agent[OrchestratorContext](
        name="narrator",
        handoff_description=(
            "Use this agent when the user wants a wrap-up, summary, recap, "
            "report, or 'what did we find' overview of the session."
        ),
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
    """Generate a wrap-up narrative for this session.

    `text` is optional — if the narrator is invoked via a 'narrate' command
    we synthesise a default prompt; if invoked via router handoff, we pass
    the user's actual utterance through.
    """
    ctx = OrchestratorContext(session_id=sid)
    user_text = text or (
        "Give me a short wrap-up of what we found this session, with the "
        "dataset state, the EDA findings, the training verdict (if any), "
        "and what to do next."
    )
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
