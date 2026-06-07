"""Training-monitor agent.

Routes through CoreWeave Qwen (with OpenAI fallback), same gotcha as the EDA
agent: NO output_type=, prompt-and-parse to a JSON object.

Tools:
    get_run_history_tool(run_id)        — fetch (W&B or replay) history
    analyze_curve_tool(metrics)         — compute overfitting / leakage signals

Final structured output (parsed from the agent's fenced JSON):

    TrainingMonitorOutput:
        speech            : ONE concise sentence citing real numbers
        verdict           : healthy | overfitting | underfitting | leakage
        run_id            : str
        step              : int      # best_val_loss_step for the verdict card
        rationale         : list[str]  # the analyze_curve bullets
        suggested_action  : stop_training | keep_training | tune_lr |
                            add_regularization | None
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
from backend.a2ui import surfaces as a2ui_surfaces
from backend.agents.context import OrchestratorContext
from backend.agents.parse import parse_json_object
from backend.agents.run_registry import get_active as get_active_run
from backend.tools import redis_state
from backend.tools.wandb_history import analyze_curve as _analyze_curve_impl
from backend.tools.wandb_history import get_run_history as _get_run_history_impl

log = logging.getLogger("hololab.agents.training_monitor")


# ---------------------------------------------------------------------------
# Weave-traced ops
# ---------------------------------------------------------------------------

@weave.op()
def op_get_run_history(run_id: str, session_id: str | None = None) -> dict[str, Any]:
    return _get_run_history_impl(run_id, session_id=session_id)


@weave.op()
def op_analyze_curve(run_id: str, metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return _analyze_curve_impl(metrics)


# ---------------------------------------------------------------------------
# @function_tool wrappers
# ---------------------------------------------------------------------------

@function_tool
async def get_run_history_tool(ctx: RunContextWrapper[OrchestratorContext],
                               run_id: str | None = None) -> dict[str, Any]:
    """Fetch the run history (config + metric rows + summary).

    Pass `run_id` only if the user mentioned a specific id; otherwise leave it
    unset and the active session run will be used.

    Returns a COMPACT version (config + summary + downsampled metrics) to keep
    LLM context small. The full metrics list is still cached in the session.
    """
    rid = run_id or ctx.context.active_run_id or get_active_run(ctx.context.session_id)
    h = op_get_run_history(rid, session_id=ctx.context.session_id)
    # cache full history into Redis scratch for follow-up tool calls
    redis_state.set_scratch(ctx.context.session_id, "training_run", h)
    # remember this run as the active one in the session
    ctx.context.active_run_id = h.get("run_id", rid)
    metrics = h.get("metrics", [])
    # downsample so the LLM context isn't blown by 600 rows
    if len(metrics) > 30:
        step = max(1, len(metrics) // 30)
        sampled = metrics[::step] + [metrics[-1]]
    else:
        sampled = metrics
    return {
        "run_id": h.get("run_id"),
        "source": h.get("source"),
        "config": h.get("config", {}),
        "summary": h.get("summary", {}),
        "metrics_sample": sampled,
        "n_metrics": len(metrics),
    }


@function_tool
async def analyze_curve_tool(ctx: RunContextWrapper[OrchestratorContext]
                             ) -> dict[str, Any]:
    """Run numerical overfitting / leakage / early-stop analysis on the
    currently cached training run. Call get_run_history first.

    Returns: {verdict, rationale[], best_val_loss, best_val_loss_step,
              final_train_loss, final_val_loss, val_minus_train_final,
              early_stop_step, below_frac, n_points}
    """
    cached = redis_state.get_scratch(ctx.context.session_id, "training_run")
    if not cached:
        return {"error": "no run cached — call get_run_history_tool first"}
    return op_analyze_curve(cached.get("run_id", ""), cached.get("metrics", []))


# ---------------------------------------------------------------------------
# Structured output (parsed, not enforced via output_type)
# ---------------------------------------------------------------------------

Verdict = Literal["healthy", "overfitting", "underfitting", "leakage", "unknown"]
SuggestedAction = Literal["stop_training", "keep_training", "tune_lr",
                          "add_regularization"]


class TrainingMonitorOutput(BaseModel):
    speech: str
    run_id: str
    verdict: Verdict
    step: int = 0
    rationale: list[str] = Field(default_factory=list)
    suggested_action: SuggestedAction | None = None


_EMPTY = TrainingMonitorOutput(
    speech="I couldn't analyze that run.",
    run_id="", verdict="unknown", step=0,
)


# ---------------------------------------------------------------------------
# Model factories — same pattern as eda.py
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
You are the training-monitor agent inside HoloLab.

CRITICAL RULES:
- You know NOTHING about the run until you call `get_run_history_tool`.
  Your FIRST action every turn MUST be a tool call to `get_run_history_tool`.
  Do NOT invent step numbers, loss values, or epochs.
- After get_run_history_tool, call `analyze_curve_tool` to get the numerical
  signals. Use ITS numbers in your final response — do not estimate.
- THEN produce the final JSON object inside a single ```json ... ``` fence.

Final JSON shape (TrainingMonitorOutput):
  {
    "speech":          "<one sentence, <=30 words, citing real numbers>",
    "run_id":          "<the actual run_id from get_run_history_tool>",
    "verdict":         "healthy" | "overfitting" | "underfitting" | "leakage",
    "step":            <best_val_loss_step from analyze_curve_tool>,
    "rationale":       ["<the bullets from analyze_curve_tool>", ...],
    "suggested_action": "stop_training" | "keep_training" |
                        "tune_lr" | "add_regularization" | null
  }

Verdict rules (use the analyze_curve_tool fields, do NOT re-derive):
  - if `verdict` from analyze_curve_tool is "leakage": copy it verbatim;
    speech should call out "val is below train on N% of steps".
  - if "overfitting": speech cites best_val_loss + the rise to final_val_loss.
    suggested_action: "stop_training".
  - if "healthy" and val still trending down at the end: suggested_action
    "keep_training". If it has plateaued: "keep_training" still fine.
  - if "underfitting" (both losses still high): suggested_action "tune_lr"
    or "add_regularization" — pick one.

Embedded A2UI surface template (for context — the orchestrator builds the
actual surface from your output):

  training-verdict:
"""


def _make_agent(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    instructions = (
        _SYSTEM_PROMPT
        + "    "
        + a2ui_surfaces.PROMPT_TEMPLATES["training-verdict"].replace("\n", "\n    ")
        + "\n"
    )
    return Agent[OrchestratorContext](
        name="training_monitor",
        handoff_description=(
            "Use this agent when the user asks about training runs, "
            "training curves, loss/accuracy, overfitting, early stopping, "
            "or data leakage."
        ),
        instructions=instructions,
        model=model,
        model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
        tools=[get_run_history_tool, analyze_curve_tool],
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
# Public entry points
# ---------------------------------------------------------------------------

@weave.op()
async def run_for_query(sid: str, text: str, *,
                         run_id: str | None = None) -> TrainingMonitorOutput:
    ctx = OrchestratorContext(session_id=sid, active_run_id=run_id or get_active_run(sid))
    return await _run_with_fallback(text, ctx)


async def _run_with_fallback(text: str, ctx: OrchestratorContext) -> TrainingMonitorOutput:
    for label, getter in (("primary", get_agent), ("fallback", get_fallback_agent)):
        try:
            agent = getter()
            result = await Runner.run(agent, text, context=ctx, max_turns=8)
            raw = result.final_output if isinstance(result.final_output, str) \
                else str(result.final_output)
            return _parse_output(raw, ctx)
        except Exception as e:  # noqa: BLE001
            log.warning("training_monitor %s agent failed: %s", label, e)
            continue
    return _EMPTY


def _parse_output(raw: str, ctx: OrchestratorContext) -> TrainingMonitorOutput:
    data = parse_json_object(raw)
    if data is None:
        # degrade to speech-only
        return TrainingMonitorOutput(
            speech=(raw or "I could not produce a structured verdict.")[:300],
            run_id=ctx.active_run_id or "",
            verdict="unknown", step=0,
        )
    try:
        return TrainingMonitorOutput.model_validate(data)
    except Exception as e:  # noqa: BLE001
        log.warning("training_monitor validation failed: %s — degrading", e)
        return TrainingMonitorOutput(
            speech=str(data.get("speech") or raw)[:300],
            run_id=str(data.get("run_id") or ctx.active_run_id or ""),
            verdict=data.get("verdict") if data.get("verdict") in
                ("healthy","overfitting","underfitting","leakage","unknown") else "unknown",
            step=int(data.get("step") or 0),
            rationale=list(data.get("rationale") or []),
            suggested_action=data.get("suggested_action") if data.get("suggested_action") in
                ("stop_training","keep_training","tune_lr","add_regularization") else None,
        )
