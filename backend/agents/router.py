"""Router agent.

Routes a voice_query to the right specialist (EDA vs training-monitor) via
OpenAI Agents SDK handoffs. The handoff itself fires a HANDOFF AG-UI event
on /agui via RunHooks so the dashboard can visualise orchestration.

Routing rule of thumb:
    "what's wrong with the data / columns / outliers / missing"  → EDA
    "is my model overfitting / loss / training / accuracy / run"  → training

The router agent has NO tools of its own — it only chooses which specialist
to invoke. After the handoff fires, the specialist agent does all the work.
"""
from __future__ import annotations

import logging
from typing import Any

import weave
from agents import (
    Agent, ModelSettings, RunHooks, Runner, handoff,
)
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from backend import config
from backend.a2ui import emitter as a2ui_emitter
from backend.agents import eda as eda_agent_mod
from backend.agents import narrator as narrator_agent_mod
from backend.agents import training_monitor as tm_agent_mod
from backend.agents.context import OrchestratorContext
from backend.agents.parse import parse_json_object
from backend.agents.eda import EDAOutput, _parse_eda_output  # noqa: F401
from backend.agents.narrator import NarratorOutput, _parse_output as _parse_narr
from backend.agents.training_monitor import TrainingMonitorOutput, _parse_output as _parse_tm

log = logging.getLogger("hololab.agents.router")


# ---------------------------------------------------------------------------
# Hooks — emit HANDOFF AG-UI events
# ---------------------------------------------------------------------------

class HoloRunHooks(RunHooks[OrchestratorContext]):
    """Stream router lifecycle into the AG-UI bus.

    We emit:
      - HANDOFF event when control transfers to a specialist
      - AGENT_THINKING when a new agent starts an LLM call (debug aid)

    We record the *last* handoff target on the context so the orchestrator
    knows which output parser to use.
    """

    async def on_handoff(self, context, from_agent, to_agent) -> None:  # type: ignore[override]
        log.info("handoff: %s → %s", from_agent.name, to_agent.name)
        context.context.scratch["last_handoff_to"] = to_agent.name
        a2ui_emitter.emit_agui(
            "HANDOFF",
            {"from": from_agent.name, "to": to_agent.name,
             "session_id": context.context.session_id},
            agent="router",
        )

    async def on_agent_start(self, context, agent) -> None:  # type: ignore[override]
        # First time the router itself starts is noisy; only emit for specialists.
        if agent.name != "router":
            a2ui_emitter.emit_agui(
                "AGENT_THINKING",
                {"agent": agent.name},
                agent=agent.name,
            )


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def _coreweave_model() -> OpenAIChatCompletionsModel:
    client = AsyncOpenAI(
        base_url=config.WANDB_INFERENCE_BASE,
        api_key=config.WANDB_API_KEY,
        default_headers={"User-Agent": "hololab-backend/0.0.2"},
    )
    return OpenAIChatCompletionsModel(model=config.ROUTER_MODEL, openai_client=client)


def _openai_fallback_model() -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(
        model=config.FALLBACK_MODEL,
        openai_client=AsyncOpenAI(api_key=config.OPENAI_API_KEY),
    )


# ---------------------------------------------------------------------------
# Router agent
# ---------------------------------------------------------------------------

_ROUTER_PROMPT = """\
You are the HoloLab router agent. You receive a single user utterance and you
MUST hand off to exactly one specialist agent — you do NOT answer the user
yourself.

Specialists:
  eda                Exploratory data analysis: columns, missing values,
                     outliers, skew, correlations, distribution shape, the
                     dataset itself, individual rows / data points.
  training_monitor   Training runs: loss curves, val vs train, overfitting,
                     early stopping, leakage, suggested next epoch, learning
                     rate, optimizer, the model's behaviour during training.
  narrator           Session wrap-up / recap / summary: any utterance asking
                     "what did we find", "summarise", "wrap up", "give me a
                     report", "tell me the story so far".

Decision rules:
  - Words like "summary", "summarise", "wrap", "recap", "report", "story",
    "what did we", "tell me everything", "overview" → narrator.
  - Words like "column", "missing", "outliers", "skewed", "rows", "dataset",
    "feature", "distribution" → eda.
  - Words like "training", "loss", "val", "validation", "accuracy",
    "overfitting", "epoch", "step", "run", "model is" → training_monitor.
  - When ambiguous, prefer eda only if the utterance mentions data; otherwise
    training_monitor.

Hand off NOW. Do not write any prose. Do not call any tool other than the
handoff itself.
"""


def _make_router(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    return Agent[OrchestratorContext](
        name="router",
        instructions=_ROUTER_PROMPT,
        model=model,
        model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
        handoffs=[
            handoff(agent=eda_agent_mod.get_agent()),
            handoff(agent=tm_agent_mod.get_agent()),
            handoff(agent=narrator_agent_mod.get_agent()),
        ],
    )


_router: Agent[OrchestratorContext] | None = None
_router_fb: Agent[OrchestratorContext] | None = None


def get_router() -> Agent[OrchestratorContext]:
    global _router
    if _router is None:
        _router = _make_router(_coreweave_model())
    return _router


def get_router_fallback() -> Agent[OrchestratorContext]:
    global _router_fb
    if _router_fb is None:
        # Use OpenAI fallback specialist agents too, to keep the graph self-consistent.
        fb = Agent[OrchestratorContext](
            name="router",
            instructions=_ROUTER_PROMPT,
            model=_openai_fallback_model(),
            model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
            handoffs=[
                handoff(agent=eda_agent_mod.get_fallback_agent()),
                handoff(agent=tm_agent_mod.get_fallback_agent()),
                handoff(agent=narrator_agent_mod.get_fallback_agent()),
            ],
        )
        _router_fb = fb
    return _router_fb


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class RouterResult:
    """Lightweight wrapper around whichever specialist's output we got."""

    def __init__(self, *, target: str,
                 eda: EDAOutput | None = None,
                 training: TrainingMonitorOutput | None = None,
                 narrator: NarratorOutput | None = None) -> None:
        self.target = target            # "eda" | "training_monitor" | "narrator" | "unknown"
        self.eda = eda
        self.training = training
        self.narrator = narrator


@weave.op()
async def route(sid: str, text: str, ctx: OrchestratorContext) -> RouterResult:
    """Run the router; return parsed result keyed by the agent that answered."""
    hooks = HoloRunHooks()
    raw: str = ""
    for label, getter in (("primary", get_router), ("fallback", get_router_fallback)):
        try:
            agent = getter()
            result = await Runner.run(agent, text, context=ctx, max_turns=10, hooks=hooks)
            raw = result.final_output if isinstance(result.final_output, str) \
                else str(result.final_output)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("router %s failed: %s", label, e)
    else:
        # Both failed — nothing more to do.
        return RouterResult(target="unknown")

    target = ctx.scratch.get("last_handoff_to") or _infer_target(text)
    log.info("router target=%s (raw len=%d)", target, len(raw))

    if target == "training_monitor":
        return RouterResult(target=target, training=_parse_tm(raw, ctx))
    elif target == "eda":
        return RouterResult(target=target, eda=_parse_eda_output(raw))
    elif target == "narrator":
        return RouterResult(target=target, narrator=_parse_narr(raw))
    else:
        # Fallback: try all parsers — caller picks what's non-empty.
        return RouterResult(target="unknown",
                            eda=_parse_eda_output(raw),
                            training=_parse_tm(raw, ctx),
                            narrator=_parse_narr(raw))


def _infer_target(text: str) -> str:
    """Cheap keyword-based fallback when no handoff was observed."""
    t = text.lower()
    narr_kw = ("summar", "wrap", "recap", "report", "story so far",
               "what did we", "overview", "tell me everything")
    train_kw = ("overfit", "loss", "val ", "training", "train_", "accuracy",
                "epoch", "step", "leak", "run", "early stop")
    eda_kw = ("column", "missing", "outlier", "skew", "row", "dataset",
              "feature", "distribution")
    if any(k in t for k in narr_kw):
        return "narrator"
    if any(k in t for k in train_kw):
        return "training_monitor"
    if any(k in t for k in eda_kw):
        return "eda"
    return "eda"  # safe default
