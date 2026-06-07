"""Problem-type agent — read-only classification vs regression framing.

Uses OpenAI gpt-4o-mini (not CoreWeave). Prompt-and-parse only — NO output_type=.

Tool:
    get_target_info_tool   inspect likely target column (pandas, no LLM)

Structured output (parsed from fenced JSON):
    ProblemTypeOutput:
        speech             one-line TTS answer
        problem_type       classification | regression | unknown
        target_column      str | null
        model_suggestion   str
        reason             str
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

import pandas as pd
import weave
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from backend import config
from backend.agents.context import OrchestratorContext
from backend.agents.parse import parse_json_object

log = logging.getLogger("hololab.agents.problem_type")

_TARGET_NAMES = frozenset({
    "target", "label", "y", "class", "category", "survived", "outcome",
})

ProblemKind = Literal["classification", "regression", "unknown"]

_MISSING_EXCLUDE_PCT = 30.0
_HIGH_CARD_FRAC = 0.5


def _missing_pct(series: pd.Series) -> float:
    return float(series.isna().mean() * 100.0)


def _is_excluded_target_candidate(series: pd.Series, n_rows: int) -> bool:
    if _missing_pct(series) > _MISSING_EXCLUDE_PCT:
        return True
    n_unique = int(series.nunique(dropna=True))
    return n_unique > max(1, int(n_rows * _HIGH_CARD_FRAC))


def _is_likely_label_column(series: pd.Series, n_rows: int) -> bool:
    if _is_excluded_target_candidate(series, n_rows):
        return False
    n_unique = int(series.nunique(dropna=True))
    if pd.api.types.is_bool_dtype(series):
        return True
    if pd.api.types.is_integer_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return n_unique <= 20
    if pd.api.types.is_object_dtype(series) or isinstance(series.dtype, pd.CategoricalDtype):
        return n_unique <= 20
    return False


def _pick_target_column(df: pd.DataFrame) -> str:
    for col in df.columns:
        if str(col).lower() in _TARGET_NAMES:
            return str(col)
    candidates: list[tuple[float, int, str]] = []
    n_rows = len(df)
    for col in df.columns:
        series = df[col]
        if not _is_likely_label_column(series, n_rows):
            continue
        candidates.append((_missing_pct(series), int(series.nunique(dropna=True)), str(col)))
    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][2]
    return str(df.columns[-1])


# ---------------------------------------------------------------------------
# Weave-traced op (pure pandas)
# ---------------------------------------------------------------------------

@weave.op()
def op_get_target_info(dataset_name: str, df: pd.DataFrame | None) -> dict[str, Any]:
    """Identify the likely target column and return dtype / cardinality / samples."""
    if df is None or df.empty:
        return {
            "error": "no dataset loaded",
            "dataset_name": dataset_name,
            "target_column": None,
            "dtype": None,
            "n_unique": 0,
            "sample_values": [],
        }

    target_col = _pick_target_column(df)
    series = df[target_col]
    samples = [
        None if pd.isna(v) else (v.item() if hasattr(v, "item") else v)
        for v in series.dropna().head(5).tolist()
    ]
    return {
        "dataset_name": dataset_name,
        "target_column": target_col,
        "dtype": str(series.dtype),
        "n_unique": int(series.nunique(dropna=True)),
        "sample_values": samples,
        "n_rows": len(df),
    }


# ---------------------------------------------------------------------------
# @function_tool wrapper
# ---------------------------------------------------------------------------

@function_tool
async def get_target_info_tool(ctx: RunContextWrapper[OrchestratorContext]) -> dict[str, Any]:
    """Inspect the active dataset's likely target column.

    Prefers named targets (target/label/y/category/survived/...); else a
    low-cardinality label column (excludes >30% missing and high-cardinality
    text); else the last column. Returns dtype, n_unique, and sample values.
    """
    return op_get_target_info(ctx.context.dataset_name, ctx.context.df)


# ---------------------------------------------------------------------------
# Structured output (parsed, not enforced via output_type)
# ---------------------------------------------------------------------------

class ProblemTypeOutput(BaseModel):
    speech: str
    problem_type: ProblemKind = "unknown"
    target_column: str | None = None
    model_suggestion: str = ""
    reason: str = ""


_EMPTY = ProblemTypeOutput(
    speech=("I couldn't identify a clear target column — "
            "which column are we predicting?"),
    problem_type="unknown",
    target_column=None,
    model_suggestion="",
    reason="no target column identified",
)


# ---------------------------------------------------------------------------
# Model — OpenAI gpt-4o-mini only for this agent
# ---------------------------------------------------------------------------

def _openai_model() -> OpenAIChatCompletionsModel:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; problem_type agent requires OpenAI")
    return OpenAIChatCompletionsModel(
        model=config.FALLBACK_MODEL,  # gpt-4o-mini
        openai_client=AsyncOpenAI(api_key=config.OPENAI_API_KEY),
    )


_SYSTEM_PROMPT = """\
You are the problem-type agent inside HoloLab — a read-only specialist.

CRITICAL RULES:
- You know NOTHING about the dataset until you call `get_target_info_tool`.
  Your FIRST action every turn MUST be a tool call to `get_target_info_tool`.
  Do NOT invent column names, dtypes, or cardinalities.
- If the tool returns an error or target_column is null, respond gracefully:
  speech must ask which column is the prediction target; set problem_type to
  "unknown" and leave model_suggestion empty.
- Otherwise reason over the tool output and produce the final JSON inside a
  single ```json ... ``` fence.

Heuristic guidance (use the tool numbers, do not guess):
  - classification: object dtype, or integer with low n_unique (e.g. <= 20),
    or obvious categorical / binary target (2 unique values → binary).
  - regression: continuous float with many unique values relative to n_rows.
  - when ambiguous, pick the best fit and explain briefly in reason.

Final JSON shape (ProblemTypeOutput):
  {
    "speech": "<one spoken sentence, <=35 words, cite target column name>",
    "problem_type": "classification" | "regression" | "unknown",
    "target_column": "<from tool or null>",
    "model_suggestion": "<ONE model name only, e.g. logistic regression>",
    "reason": "<ONE short reason, <=20 words>"
  }

speech examples:
  - "This looks like a classification problem on 'survived' — try logistic "
    "regression to start, since the target is binary."
  - "This looks like a regression problem on 'fare' — try linear regression "
    "first, since the target is continuous with many unique values."

Return exactly one model in model_suggestion. Keep speech conversational.
"""


def _make_agent(model: OpenAIChatCompletionsModel) -> Agent[OrchestratorContext]:
    example = ProblemTypeOutput(
        speech="This looks like a classification problem on 'survived' — try "
               "logistic regression to start, since the target is binary.",
        problem_type="classification",
        target_column="survived",
        model_suggestion="logistic regression",
        reason="binary categorical target",
    )
    instructions = (
        _SYSTEM_PROMPT
        + "\nExample output:\n```json\n"
        + json.dumps(example.model_dump(), indent=2)
        + "\n```\n"
    )
    return Agent[OrchestratorContext](
        name="problem_type",
        handoff_description=(
            "Use this agent when the user asks what kind of ML problem they "
            "are solving, whether the task is classification or regression, "
            "what the target column is, or what model to start with."
        ),
        instructions=instructions,
        model=model,
        model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
        tools=[get_target_info_tool],
    )


_agent: Agent[OrchestratorContext] | None = None


def get_agent() -> Agent[OrchestratorContext]:
    global _agent
    if _agent is None:
        _agent = _make_agent(_openai_model())
    return _agent


def get_fallback_agent() -> Agent[OrchestratorContext]:
    """Same OpenAI-backed agent — kept for router graph symmetry."""
    return get_agent()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

@weave.op()
async def run_for_query(
    sid: str, text: str, df: pd.DataFrame | None, dataset_name: str,
) -> ProblemTypeOutput:
    ctx = OrchestratorContext(session_id=sid, dataset_name=dataset_name, df=df)
    return await _run(text, ctx)


async def _run(text: str, ctx: OrchestratorContext) -> ProblemTypeOutput:
    try:
        agent = get_agent()
        result = await Runner.run(agent, text, context=ctx, max_turns=6)
        raw = result.final_output if isinstance(result.final_output, str) \
            else str(result.final_output)
        return _parse_output(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("problem_type agent failed: %s", e)
        return _EMPTY


def _parse_output(raw: str) -> ProblemTypeOutput:
    data = parse_json_object(raw)
    if data is None:
        text = (raw or "").strip()
        if text:
            return ProblemTypeOutput(
                speech=text[:300],
                problem_type="unknown",
            )
        return _EMPTY
    try:
        return ProblemTypeOutput.model_validate(data)
    except ValidationError as e:
        log.warning("problem_type validation failed: %s — degrading", e)
        pt = data.get("problem_type")
        if pt not in ("classification", "regression", "unknown"):
            pt = "unknown"
        return ProblemTypeOutput(
            speech=str(data.get("speech") or raw)[:300],
            problem_type=pt,
            target_column=data.get("target_column"),
            model_suggestion=str(data.get("model_suggestion") or ""),
            reason=str(data.get("reason") or ""),
        )
