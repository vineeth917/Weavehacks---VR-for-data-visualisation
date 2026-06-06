"""EDA agent — OpenAI Agents SDK, routed through CoreWeave W&B inference.

Model: `Qwen/Qwen3-235B-A22B-Instruct-2507` (bench winner, MoE, ~0.4s typical).
Offline fallback: OpenAI `gpt-4o-mini` (per config.FALLBACK_MODEL).

Tools (all wrapped in `@weave.op()` so every call shows up in the Weave trace):
    profile_dataset_tool   no-arg; profiles the session's active dataset
    flag_columns_tool      arg=flag; returns matching column names
    inspect_rows_tool      arg=row_ids; returns raw row dicts for selected points

Structured output (`EDAOutput`):
    speech                 ≤ 1 sentence to TTS
    panel_specs            up to 4 columns × {histogram|box|kde|corr|missing}
    findings               list of {column, flag, note} for the eda-findings surface
    transform_suggestion   optional {column, transform} for the eda-action card
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
from backend.a2ui import surfaces as a2ui_surfaces
from backend.agents.context import OrchestratorContext
from backend.agents.parse import parse_json_object
from backend.tools import redis_state
from backend.tools.profiling import flag_columns as _flag_columns_impl
from backend.tools.profiling import profile_dataset as _profile_dataset_impl

log = logging.getLogger("hololab.agents.eda")


# Back-compat alias — old code imported EDAContext from this module.
EDAContext = OrchestratorContext


# ---------------------------------------------------------------------------
# Weave-traced underlying ops (the @function_tool wrappers below call these,
# so each agent tool call gets one nice op span in the trace)
# ---------------------------------------------------------------------------

@weave.op()
def op_profile_dataset(dataset_name: str, df: pd.DataFrame) -> dict[str, Any]:
    return _profile_dataset_impl(df)


@weave.op()
def op_flag_columns(dataset_name: str, profile: dict[str, Any], flag: str) -> list[str]:
    return _flag_columns_impl(profile, flag)


@weave.op()
def op_inspect_rows(
    dataset_name: str, df: pd.DataFrame, row_ids: list[str]
) -> list[dict[str, Any]]:
    """Return raw row data for a list of `r<idx>` ids (from a 3D selection)."""
    out: list[dict[str, Any]] = []
    for rid in row_ids[:20]:
        try:
            idx = int(rid.lstrip("r"))
        except ValueError:
            continue
        if idx in df.index:
            row = df.loc[idx].to_dict()
            cleaned = {k: (None if pd.isna(v) else v) for k, v in row.items()}
            out.append({"id": rid, "data": cleaned})
    return out


# ---------------------------------------------------------------------------
# @function_tool wrappers (agent-facing)
# ---------------------------------------------------------------------------

@function_tool
async def profile_dataset_tool(ctx: RunContextWrapper[EDAContext]) -> dict[str, Any]:
    """Profile the active dataset: per-column dtype, missing %, skew, IQR outlier
    %, unique count, flags (right_skewed, left_skewed, heavy_missing,
    near_constant, high_cardinality, outliers), and top correlations.

    Call this FIRST before flag_columns or before recommending plots.
    """
    prof = op_profile_dataset(ctx.context.dataset_name, ctx.context.df)
    redis_state.set_profile(ctx.context.session_id, prof)
    # Strip the heavy columns list when echoing back to the LLM — give it a
    # compact summary so token usage stays low.
    summary = {
        "dataset_name": ctx.context.dataset_name,
        "n_rows": prof["n_rows"],
        "n_cols": prof["n_cols"],
        "columns": [
            {k: c[k] for k in ("name", "dtype", "missing_pct", "skew",
                               "outlier_pct", "flags", "plot")
             if k in c}
            for c in prof["columns"]
        ],
        "top_correlations": prof.get("top_correlations", []),
        "notes": prof.get("notes", []),
    }
    return summary


@function_tool
async def flag_columns_tool(
    ctx: RunContextWrapper[EDAContext],
    flag: Literal[
        "right_skewed", "left_skewed", "heavy_missing",
        "near_constant", "high_cardinality", "outliers",
    ],
) -> list[str]:
    """Return column names matching a flag from the cached profile."""
    prof = redis_state.get_profile(ctx.context.session_id)
    if prof is None:
        prof = op_profile_dataset(ctx.context.dataset_name, ctx.context.df)
        redis_state.set_profile(ctx.context.session_id, prof)
    return op_flag_columns(ctx.context.dataset_name, prof, flag)


@function_tool
async def inspect_rows_tool(
    ctx: RunContextWrapper[EDAContext],
    row_ids: list[str],
) -> list[dict[str, Any]]:
    """Return raw row data for selected points (up to 20)."""
    return op_inspect_rows(ctx.context.dataset_name, ctx.context.df, row_ids)


# ---------------------------------------------------------------------------
# Structured output schema (Agents SDK output_type=)
# ---------------------------------------------------------------------------

PanelKind = Literal["histogram", "box", "kde", "corr", "missing"]
PositionHint = Literal["left", "right", "center", "above", "below"]


class PanelSpec(BaseModel):
    """One panel to render. The orchestrator turns this into a real Panel
    (with image_b64 PNG) before sending over /ws."""
    column: str | None = None
    kind: PanelKind
    title: str | None = None
    flags: list[str] = Field(default_factory=list)
    position_hint: PositionHint = "center"


class FindingRow(BaseModel):
    column: str
    flag: str
    note: str = ""


class TransformSuggestion(BaseModel):
    column: str
    transform: Literal["log", "sqrt", "boxcox", "impute_median",
                       "impute_mode", "drop"] = "log"


class EDAOutput(BaseModel):
    speech: str
    panel_specs: list[PanelSpec] = Field(default_factory=list)
    findings: list[FindingRow] = Field(default_factory=list)
    transform_suggestion: TransformSuggestion | None = None


# ---------------------------------------------------------------------------
# Model factory — CoreWeave first, OpenAI fallback
# ---------------------------------------------------------------------------

def _coreweave_model() -> OpenAIChatCompletionsModel:
    client = AsyncOpenAI(
        base_url=config.WANDB_INFERENCE_BASE,
        api_key=config.WANDB_API_KEY,
        default_headers={"User-Agent": "hololab-backend/0.0.2"},  # avoid CF WAF 1010
    )
    return OpenAIChatCompletionsModel(
        model=config.REASONING_MODEL,
        openai_client=client,
    )


def _openai_fallback_model() -> OpenAIChatCompletionsModel:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; cannot fall back")
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return OpenAIChatCompletionsModel(
        model=config.FALLBACK_MODEL,
        openai_client=client,
    )


# ---------------------------------------------------------------------------
# Prompt + agent
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_HEAD = """\
You are the EDA agent inside HoloLab — a VR data-science workspace.

CRITICAL RULES (do not skip):
- You do NOT know anything about the dataset until you call `profile_dataset`.
  Your FIRST action on every turn MUST be a tool call to `profile_dataset`.
  Do NOT invent column names, missing percentages, or skew values.
- After profile_dataset returns, you MAY call `flag_columns` 0+ times to
  enumerate columns matching a specific flag.
- If the user mentions specific rows / row IDs / a selection, call
  `inspect_rows` with those IDs.
- Only AFTER tool calls have given you concrete numbers, produce the final
  EDAOutput JSON.

Workflow (typical):
  turn 1: tool_call profile_dataset
  turn 2: tool_call flag_columns(flag="heavy_missing")  (if relevant)
  turn 3: tool_call flag_columns(flag="right_skewed")   (if relevant)
  turn 4: final EDAOutput

Output (EDAOutput, JSON):
  - speech            ONE concise sentence (<=30 words) for TTS. Cite real
                      column names from the profile, not invented ones.
  - panel_specs       up to 4 entries naming (column, kind). DO NOT set
                      image_b64; the orchestrator renders it. Allowed kinds:
                        histogram, box, kde   (numeric column)
                        corr, missing         (dataset-level; column null)
                      Prefer kde for high-cardinality numerics, histogram
                      for low-cardinality. Include `flags` if the column
                      has any (copy from the profile).
     - findings          rows for the eda-findings A2UI surface; one row per
                      flagged column you want highlighted. Each row has
                      column + flag + short note (<=12 words). Aim for 3-5
                      rows when there are interesting flags, 0 when the
                      dataset is clean.

                      WHEN A COLUMN HAS MULTIPLE FLAGS, choose the MOST
                      ACTIONABLE one for the `flag` field, using this
                      strict priority (left wins):
                          heavy_missing > near_constant > outliers
                                        > right_skewed / left_skewed
                                        > high_cardinality
                      The note can mention secondary flags briefly. E.g.
                      a column flagged [near_constant, right_skewed, high_cardinality]
                      → flag: "near_constant",
                        note: "~98% one value, low-information feature".
                      A column flagged [right_skewed, outliers] → flag:
                      "outliers", note: "heavy right tail, log candidate".
  - transform_suggestion  optional. Set ONLY when the user is targeting a
                          specific column AND a transform clearly helps
                          (e.g. log on right_skewed `fare`). Omit otherwise.

Example minimum behaviour (titanic):
  Tool: profile_dataset() → returns columns incl. age (miss 19.9%),
        deck (miss 77.2%), fare (skew 4.79, outliers).
  Output:
    speech: "Deck is mostly missing, fare is right-skewed with outliers, age is missing for 20% of rows."
    panel_specs: [{kind:"missing"}, {column:"fare", kind:"kde", flags:["right_skewed","outliers"]}]
    findings: [{column:"deck", flag:"heavy_missing", note:"77% missing"},
               {column:"fare", flag:"right_skewed", note:"log candidate"},
               {column:"age", flag:"heavy_missing", note:"20% missing"}]

Embedded A2UI surface templates (the orchestrator constructs the actual
surfaces from your structured output; these are for your reference):

  eda-findings:
"""


def _make_agent(model: OpenAIChatCompletionsModel) -> Agent[EDAContext]:
    instructions = (
        _SYSTEM_PROMPT_HEAD
        + "    "
        + a2ui_surfaces.PROMPT_TEMPLATES["eda-findings"].replace("\n", "\n    ")
        + "\n\n  eda-action:\n    "
        + a2ui_surfaces.PROMPT_TEMPLATES["eda-action"].replace("\n", "\n    ")
        + "\n\n"
        + "Final output format (return exactly this JSON object after your tool"
          " calls, wrapped in ```json ... ``` fences):\n"
        + "```json\n"
        + json.dumps(EDAOutput(speech="...", panel_specs=[], findings=[]).model_dump(), indent=2)
        + "\n```\n"
    )
    return Agent[EDAContext](
        name="eda",
        instructions=instructions,
        model=model,
        model_settings=ModelSettings(temperature=0.0, parallel_tool_calls=False),
        tools=[profile_dataset_tool, flag_columns_tool, inspect_rows_tool],
        # NOTE: do NOT set output_type=EDAOutput — the SDK then uses
        # response_format strict mode, which Qwen-on-CoreWeave short-circuits
        # to *immediately* without ever calling tools. We parse the JSON from
        # the final text instead (see _parse_eda_output below).
    )


# Module-level singletons (built lazily so importing this module is cheap)
_primary_agent: Agent[EDAContext] | None = None
_fallback_agent: Agent[EDAContext] | None = None


def get_agent() -> Agent[EDAContext]:
    global _primary_agent
    if _primary_agent is None:
        _primary_agent = _make_agent(_coreweave_model())
    return _primary_agent


def get_fallback_agent() -> Agent[EDAContext]:
    global _fallback_agent
    if _fallback_agent is None:
        _fallback_agent = _make_agent(_openai_fallback_model())
    return _fallback_agent


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

@weave.op()
async def run_for_query(sid: str, text: str, df: pd.DataFrame, dataset_name: str) -> EDAOutput:
    """Run the EDA agent for a free-form voice/text query."""
    ctx = EDAContext(session_id=sid, dataset_name=dataset_name, df=df)
    return await _run_with_fallback(text, ctx)


@weave.op()
async def run_for_interaction(
    sid: str, action: str, point_ids: list[str] | None,
    target_id: str | None, df: pd.DataFrame, dataset_name: str,
) -> EDAOutput:
    """Run the EDA agent in response to a VR interaction (select/grab)."""
    ids = point_ids or ([target_id] if target_id else [])
    parts: list[str] = []
    if action == "select_panel":
        parts.append(f"The user just tapped the panel '{target_id}'.")
        parts.append("Briefly explain what that panel shows and whether the "
                     "column behind it has any flags.")
    else:
        parts.append(f"The user just performed '{action}' on row IDs {ids[:10]}.")
        parts.append("Call inspect_rows to look at those rows, then comment "
                     "briefly on what's notable about them.")
        parts.append("If one column clearly merits a transform (e.g. fare is "
                     "right_skewed and you saw an outlier in those rows), set "
                     "transform_suggestion.")
    ctx = EDAContext(session_id=sid, dataset_name=dataset_name, df=df)
    return await _run_with_fallback(" ".join(parts), ctx)


def _parse_eda_output(raw: str) -> EDAOutput:
    """Tolerantly extract the EDAOutput JSON from the agent's final text."""
    data = parse_json_object(raw or "")
    if data is not None:
        try:
            return EDAOutput.model_validate(data)
        except ValidationError as e:
            log.warning("EDA output validation failed: %s — degrading", e)
            # try to salvage at least speech + best-effort findings
            return EDAOutput(
                speech=str(data.get("speech") or (raw or ""))[:300],
                panel_specs=[
                    PanelSpec.model_validate(p) for p in (data.get("panel_specs") or [])
                    if isinstance(p, dict) and "kind" in p
                ][:4],
                findings=[
                    FindingRow.model_validate(f) for f in (data.get("findings") or [])
                    if isinstance(f, dict) and "column" in f and "flag" in f
                ][:8],
                transform_suggestion=None,
            )
    return EDAOutput(
        speech=(raw or "I could not produce a structured response.")[:300],
        panel_specs=[], findings=[], transform_suggestion=None,
    )


async def _run_with_fallback(text: str, ctx: EDAContext) -> EDAOutput:
    """Try primary (CoreWeave Qwen); on failure fall back to OpenAI."""
    try:
        agent = get_agent()
        result = await Runner.run(agent, text, context=ctx, max_turns=8)
        raw = result.final_output if isinstance(result.final_output, str) else str(result.final_output)
        return _parse_eda_output(raw)
    except Exception as primary_err:  # noqa: BLE001
        log.warning("primary EDA agent failed (%s) — trying fallback",
                    type(primary_err).__name__)
        try:
            agent = get_fallback_agent()
            result = await Runner.run(agent, text, context=ctx, max_turns=8)
            raw = result.final_output if isinstance(result.final_output, str) else str(result.final_output)
            return _parse_eda_output(raw)
        except Exception as fb_err:  # noqa: BLE001
            log.error("EDA fallback also failed: %s", fb_err)
            return EDAOutput(
                speech=("I had trouble reasoning about this dataset. "
                        "Try a more specific question."),
                panel_specs=[],
                findings=[],
                transform_suggestion=None,
            )
