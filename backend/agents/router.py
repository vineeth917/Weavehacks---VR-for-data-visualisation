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
from backend.agents import problem_type as problem_type_agent_mod
from backend.agents import training_monitor as tm_agent_mod
from backend.agents import evals as evals_agent_mod
from backend.agents import preprocessor as preprocessor_agent_mod
from backend.agents import trainer as trainer_agent_mod
from backend.agents.context import OrchestratorContext
from backend.agents.parse import parse_json_object
from backend.agents.eda import EDAOutput, _parse_eda_output  # noqa: F401
from backend.agents.narrator import NarratorOutput, _parse_output as _parse_narr
from backend.agents.evals import EvalsOutput
from backend.agents.trainer import TrainerOutput
from backend.agents.preprocessor import PreprocessorOutput, _parse_output as _parse_pp
from backend.agents.problem_type import ProblemTypeOutput, _parse_output as _parse_pt
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

_ROUTER_PROMPT_BASE = """\
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
  problem_type       Problem framing: classification vs regression, what is
                     the target column, what model to start with, what problem
                     are we solving.
{preprocessor_block}{evals_block}{trainer_block}
Decision rules:
  - Words like "summary", "summarise", "wrap", "recap", "report", "story",
    "what did we", "tell me everything", "overview" → narrator.
{preprocessor_rules}{evals_rules}{trainer_rules}
  - Words like "column", "missing", "outliers", "skewed", "rows", "dataset",
    "feature", "distribution" (when asking what is wrong, not to fix) → eda.
  - Words like "training", "loss", "val", "validation", "accuracy",
    "overfitting", "epoch", "step", "run", "model is" → training_monitor.
  - Words like "problem", "classification", "regression", "predict",
    "target", "what model", "which model", "solving" → problem_type.
  - When ambiguous, prefer eda only if the utterance mentions data; otherwise
    training_monitor.

Hand off NOW. Do not write any prose. Do not call any tool other than the
handoff itself.
"""

_PREPROCESSOR_BLOCK = """\
  preprocessor       Data cleaning / transforms: remove nulls, drop duplicates,
                     clip outliers, log-transform skew, scale numerics, one-hot
                     encode, balance classes, "make data ready for training",
                     or read-only "is my data ready to train?" checks.
"""

_PREPROCESSOR_RULES = """\
  - Words like "remove", "drop", "clean", "transform", "log", "scale",
    "encode", "resample", "balance", "preprocess", "ready to train",
    "ready for training", "make the data ready" → preprocessor.
"""

_EVALS_BLOCK = """\
  evals                Model evaluation on the cleaned session data: run evals,
                     show results, confusion matrix, test accuracy, RMSE/MAE/R².
"""

_EVALS_RULES = """\
  - Words like "evals", "results", "confusion", "metrics", "score",
    "performance", "accuracy", "how did", "run the evals" → evals.
"""

_TRAINER_BLOCK = """\
  trainer              Iterative sklearn training on the cleaned session data:
                     train the model, start training, fit the model.
"""

_TRAINER_RULES = """\
  - Words like "train the model", "train a model", "start training",
    "fit the model", "training the model" → trainer (NOT training_monitor).
"""


def _router_prompt() -> str:
    return _ROUTER_PROMPT_BASE.format(
        preprocessor_block=_PREPROCESSOR_BLOCK if config.ENABLE_PREPROCESSOR else "",
        preprocessor_rules=_PREPROCESSOR_RULES if config.ENABLE_PREPROCESSOR else "",
        evals_block=_EVALS_BLOCK if config.ENABLE_EVALS else "",
        evals_rules=_EVALS_RULES if config.ENABLE_EVALS else "",
        trainer_block=_TRAINER_BLOCK if config.ENABLE_TRAINER else "",
        trainer_rules=_TRAINER_RULES if config.ENABLE_TRAINER else "",
    )


def _router_handoffs() -> list[Any]:
    hs = [
        handoff(agent=eda_agent_mod.get_agent()),
        handoff(agent=tm_agent_mod.get_agent()),
        handoff(agent=narrator_agent_mod.get_agent()),
        handoff(agent=problem_type_agent_mod.get_agent()),
    ]
    if config.ENABLE_PREPROCESSOR:
        hs.append(handoff(agent=preprocessor_agent_mod.get_agent()))
    if config.ENABLE_EVALS:
        hs.append(handoff(agent=evals_agent_mod.get_agent()))
    if config.ENABLE_TRAINER:
        hs.append(handoff(agent=trainer_agent_mod.get_agent()))
    return hs


def _make_router(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    return Agent[OrchestratorContext](
        name="router",
        instructions=_router_prompt(),
        model=model,
        model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
        handoffs=_router_handoffs(),
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
        fb_handoffs = [
            handoff(agent=eda_agent_mod.get_fallback_agent()),
            handoff(agent=tm_agent_mod.get_fallback_agent()),
            handoff(agent=narrator_agent_mod.get_fallback_agent()),
            handoff(agent=problem_type_agent_mod.get_fallback_agent()),
        ]
        if config.ENABLE_PREPROCESSOR:
            fb_handoffs.append(handoff(agent=preprocessor_agent_mod.get_fallback_agent()))
        if config.ENABLE_EVALS:
            fb_handoffs.append(handoff(agent=evals_agent_mod.get_fallback_agent()))
        if config.ENABLE_TRAINER:
            fb_handoffs.append(handoff(agent=trainer_agent_mod.get_fallback_agent()))
        fb = Agent[OrchestratorContext](
            name="router",
            instructions=_router_prompt(),
            model=_openai_fallback_model(),
            model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
            handoffs=fb_handoffs,
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
                 narrator: NarratorOutput | None = None,
                 problem_type: ProblemTypeOutput | None = None,
                 preprocessor: PreprocessorOutput | None = None,
                 evals: EvalsOutput | None = None,
                 trainer: TrainerOutput | None = None) -> None:
        self.target = target            # "eda" | "training_monitor" | "narrator" | ...
        self.eda = eda
        self.training = training
        self.narrator = narrator
        self.problem_type = problem_type
        self.preprocessor = preprocessor
        self.evals = evals
        self.trainer = trainer


async def _dispatch_keyword_fallback(sid: str, text: str,
                                     ctx: OrchestratorContext) -> RouterResult:
    """Run a specialist directly when the LLM router cannot complete a handoff."""
    target = _infer_target(text)
    log.info("router keyword fallback → %s", target)
    if target == "preprocessor" and config.ENABLE_PREPROCESSOR:
        from backend.agents import dataset_versions as _dv
        out = await preprocessor_agent_mod.run_for_query(
            sid, text, ctx.df, ctx.dataset_name,
        )
        working = _dv.get_working(sid)
        if working:
            ctx.df = working[2]
        return RouterResult(target="preprocessor", preprocessor=out)
    if target == "evals" and config.ENABLE_EVALS:
        out = await evals_agent_mod.run_for_query(
            sid, text, ctx.df, ctx.dataset_name,
        )
        return RouterResult(target="evals", evals=out)
    if target == "trainer" and config.ENABLE_TRAINER:
        out = await trainer_agent_mod.run_for_query(
            sid, text, ctx.df, ctx.dataset_name,
        )
        return RouterResult(target="trainer", trainer=out)
    return RouterResult(target="unknown")


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
        return await _dispatch_keyword_fallback(sid, text, ctx)

    target = ctx.scratch.get("last_handoff_to") or _infer_target(text)
    log.info("router target=%s (raw len=%d)", target, len(raw))

    if target == "training_monitor":
        return RouterResult(target=target, training=_parse_tm(raw, ctx))
    elif target == "eda":
        return RouterResult(target=target, eda=_parse_eda_output(raw))
    elif target == "narrator":
        return RouterResult(target=target, narrator=_parse_narr(raw))
    elif target == "problem_type":
        return RouterResult(target=target, problem_type=_parse_pt(raw))
    elif target == "preprocessor":
        return RouterResult(target=target, preprocessor=_parse_pp(raw, ctx))
    elif target == "evals" and config.ENABLE_EVALS:
        out = await evals_agent_mod.run_for_query(sid, text, ctx.df, ctx.dataset_name)
        return RouterResult(target="evals", evals=out)
    elif target == "trainer" and config.ENABLE_TRAINER:
        out = await trainer_agent_mod.run_for_query(sid, text, ctx.df, ctx.dataset_name)
        return RouterResult(target="trainer", trainer=out)
    else:
        # Fallback: try all parsers — caller picks what's non-empty.
        return RouterResult(target="unknown",
                            eda=_parse_eda_output(raw),
                            training=_parse_tm(raw, ctx),
                            narrator=_parse_narr(raw),
                            problem_type=_parse_pt(raw),
                            preprocessor=_parse_pp(raw, ctx))


def _infer_target(text: str) -> str:
    """Cheap keyword-based fallback when no handoff was observed."""
    t = text.lower()
    narr_kw = ("summar", "wrap", "recap", "report", "story so far",
               "what did we", "overview", "tell me everything")
    train_kw = ("overfit", "loss", "val ", "training", "train_", "accuracy",
                "epoch", "step", "leak", "run", "early stop")
    problem_kw = ("problem", "classification", "regression", "model",
                  "target", "predict")
    eda_kw = ("column", "missing", "outlier", "skew", "row", "dataset",
              "feature", "distribution")
    if any(k in t for k in narr_kw):
        return "narrator"
    if config.ENABLE_PREPROCESSOR:
        preprocess_kw = (
            "remove", "drop", "clean", "transform", "log", "scale", "encode",
            "resample", "balance", "ready", "preprocess", "duplicate", "null",
            "make the data ready", "ready for training", "ready to train",
        )
        if any(k in t for k in preprocess_kw):
            return "preprocessor"
    if config.ENABLE_EVALS:
        eval_kw = (
            "evals",
            "confusion",
            "metrics",
            "accuracy",
            "classification report",
            "how did the model do",
            "show me the results",
            "show the evals",
            "run the evals",
            "run evals",
        )
        if any(k in t for k in eval_kw):
            return "evals"
    if config.ENABLE_TRAINER:
        trainer_kw = (
            "train the model",
            "train a model",
            "start training",
            "fit the model",
            "training the model",
        )
        if any(k in t for k in trainer_kw):
            return "trainer"
    if any(k in t for k in train_kw):
        return "training_monitor"
    if any(k in t for k in problem_kw):
        return "problem_type"
    if any(k in t for k in eda_kw):
        return "eda"
    return "eda"  # safe default
