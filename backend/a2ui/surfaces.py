"""Author the three A2UI v0.8 surfaces used by the HoloLab agents.

Each factory returns a triple (components, data, surface_id) that the emitter
turns into  surfaceUpdate → dataModelUpdate → beginRendering  envelopes.

Surfaces:
    eda-findings       : list of {column, flag, note}, display only
    eda-action         : confirm-card "log-transform <column>?", actions:
                         confirm_transform | dismiss
    training-verdict   : card with verdict + reason, actions:
                         stop_training | keep_training

All button `action` strings come from backend.a2ui.ACTIONS — they are frozen.
"""
from __future__ import annotations

from typing import Any

from backend.a2ui import ACTIONS, BUTTON_MODE

# ---------------------------------------------------------------------------
# tiny DSL helpers — keep callers readable
# ---------------------------------------------------------------------------

def _comp(cid: str, kind: str, props: dict[str, Any]) -> dict[str, Any]:
    return {"id": cid, "component": {kind: props}}


def _text(cid: str, *, path: str | None = None,
          literal: str | None = None, hint: str | None = None) -> dict[str, Any]:
    text: dict[str, Any] = {}
    if path is not None:
        text["path"] = path
    if literal is not None:
        text["literalString"] = literal
    props: dict[str, Any] = {"text": text}
    if hint:
        props["usageHint"] = hint
    return _comp(cid, "Text", props)


def _row(cid: str, children: list[str], alignment: str = "spaceBetween") -> dict[str, Any]:
    return _comp(cid, "Row",
                 {"alignment": alignment,
                  "children": {"explicitList": children}})


def _col(cid: str, children: list[str], alignment: str = "start") -> dict[str, Any]:
    return _comp(cid, "Column",
                 {"alignment": alignment,
                  "children": {"explicitList": children}})


def _list(cid: str, *, data_binding: str, row_template_id: str) -> dict[str, Any]:
    return _comp(cid, "List",
                 {"dataBinding": data_binding, "componentId": row_template_id})


def _card(cid: str, child_id: str) -> dict[str, Any]:
    return _comp(cid, "Card", {"child": child_id})


def _button(cid: str, *, label: str, action: str,
            context: dict[str, Any] | None = None,
            variant: str = "primary",
            mode: str | None = None) -> dict[str, Any]:
    """Build a Button component, honouring the wire-shape mode.

    mode="v08" (default — A2UI_BUTTON_MODE=v08, our shipped/tested shape):
        {"action": "<name>", "context": {"contents": [{"key":..., "valueString":...}, ...]}}

    mode="v09" (A2UI v0.9 canonical action schema, per
    https://a2ui.org/concepts/actions/):
        {"action": {"event": {"name": "<name>", "context": {literal map}}}}

    The renderer side: in v08, our backend handles the userAction shape on
    /ws (UserAction model). In v09, dashboards typically POST to /action
    with `{action:{name, surfaceId, sourceComponentId, timestamp, context}}`
    — backend.main exposes that endpoint and normalises both shapes into
    the same internal Interaction. Person C should confirm the renderer
    actually emits one of those two shapes before we flip the default.
    """
    assert action in ACTIONS, f"action {action!r} not in frozen ACTIONS set"
    eff_mode = (mode or BUTTON_MODE).lower()

    if eff_mode == "v09":
        # Literal context map — values pre-resolved server-side.
        # (v0.9 also supports {"path": "/..."} bindings; we don't need them
        # here because every button's context is already concrete.)
        evt: dict[str, Any] = {"name": action}
        if context:
            evt["context"] = dict(context)
        props: dict[str, Any] = {
            "label": {"literalString": label},
            "action": {"event": evt},
            "variant": variant,
        }
        return _comp(cid, "Button", props)

    # default: v08 (current/tested)
    ctx_entries = []
    if context:
        for k, v in context.items():
            from backend.a2ui.values import encode_value
            entry = {"key": k}
            entry.update(encode_value(v))
            ctx_entries.append(entry)
    props = {
        "label": {"literalString": label},
        "action": action,
        "variant": variant,
    }
    if ctx_entries:
        props["context"] = {"contents": ctx_entries}
    return _comp(cid, "Button", props)


# ---------------------------------------------------------------------------
# Surface 1: eda-findings (display only — list of column flags)
# ---------------------------------------------------------------------------

def eda_findings(findings: list[dict[str, str]]) -> tuple[list, dict, str]:
    """findings: [{"column": "price", "flag": "right_skewed", "note": "log candidate"}]"""
    components = [
        _col("root", ["title", "findings_list"]),
        _text("title", literal="EDA findings", hint="h3"),
        _list("findings_list", data_binding="/findings",
              row_template_id="finding_row"),
        _row("finding_row", ["col_name", "col_flag"]),
        _text("col_name", path="/column"),
        _text("col_flag", path="/flag"),
    ]
    return components, {"findings": findings}, "eda-findings"


# ---------------------------------------------------------------------------
# Surface 2: eda-action (confirm card)
# ---------------------------------------------------------------------------

def eda_action(column: str, transform: str = "log") -> tuple[list, dict, str]:
    """Build a confirm-card like 'Log-transform `price`?'"""
    prompt = f"{transform.title()}-transform `{column}`?"
    rationale = (
        f"`{column}` is right-skewed — a {transform} transform usually helps "
        "downstream models converge."
    )
    components = [
        _card("root", "card_body"),
        _col("card_body", ["heading", "rationale", "buttons"]),
        _text("heading", path="/prompt", hint="h3"),
        _text("rationale", path="/rationale"),
        _row("buttons", ["confirm", "dismiss"], alignment="end"),
        _button("confirm", label="Apply transform",
                action="confirm_transform",
                context={"column": column, "transform": transform},
                variant="primary"),
        _button("dismiss", label="Not now",
                action="dismiss",
                context={"column": column},
                variant="secondary"),
    ]
    data = {"prompt": prompt, "rationale": rationale}
    return components, data, "eda-action"


# ---------------------------------------------------------------------------
# Surface 3: training-verdict (card with stop/keep buttons)
# ---------------------------------------------------------------------------

def training_verdict(*, run_id: str, verdict: str, reason: str,
                     step: int) -> tuple[list, dict, str]:
    """verdict ∈ {'overfitting','ok','underfitting'} — drives button states."""
    components = [
        _card("root", "card_body"),
        _col("card_body", ["heading", "reason", "buttons"]),
        _text("heading", path="/heading", hint="h3"),
        _text("reason", path="/reason"),
        _row("buttons", ["stop", "keep"], alignment="end"),
        _button("stop", label="Stop training",
                action="stop_training",
                context={"run_id": run_id, "step": step, "verdict": verdict},
                variant="primary" if verdict == "overfitting" else "secondary"),
        _button("keep", label="Keep going",
                action="keep_training",
                context={"run_id": run_id, "step": step, "verdict": verdict},
                variant="secondary" if verdict == "overfitting" else "primary"),
    ]
    heading = f"Verdict @ step {step}: {verdict}"
    return components, {"heading": heading, "reason": reason}, "training-verdict"


# ---------------------------------------------------------------------------
# Prompt-embeddable templates (LLM consumption)
# ---------------------------------------------------------------------------
# We embed these in the agent system prompts so the model can produce already-
# filled surface payloads in one shot when it has enough info; the in-code
# factories above are the canonical fallback.

PROMPT_TEMPLATES: dict[str, str] = {
    "eda-findings": (
        "Emit surface eda-findings with one row per flagged column.\n"
        "Data shape: {\"findings\": [{\"column\": str, \"flag\": str, \"note\": str}]}"
    ),
    "eda-action": (
        "Emit surface eda-action when a single column needs a transform.\n"
        "Data shape: {\"prompt\": str, \"rationale\": str}\n"
        "Buttons: confirm → action=confirm_transform context={column, transform};\n"
        "         dismiss → action=dismiss context={column}"
    ),
    "training-verdict": (
        "Emit surface training-verdict at most once per ~30 steps when the "
        "training-monitor changes its mind.\n"
        "Data shape: {\"heading\": str, \"reason\": str}\n"
        "Buttons: stop → action=stop_training; keep → action=keep_training; "
        "context={run_id, step, verdict}"
    ),
}
