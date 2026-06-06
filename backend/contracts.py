"""Wire-level message schemas (PLAN.md §6).

Frozen. Any change here is a stop-and-broadcast event for B & C.

All inbound messages carry a discriminator `type` and a `session_id`.
All outbound messages carry a discriminator `type`.

Version: 0.0.2  (interaction-loop + A2UI phase)
  CHANGES from 0.0.1 — see backend/CONTRACTS.md "Changelog" section:
    + Interaction.action now an open string (was Literal[select_panel,select_point]).
      Added recognised actions: select_point, grab_region, confirm_transform,
      dismiss, stop_training, keep_training.
    + Interaction.target_id and Interaction.point_ids are now both optional.
    + New outbound types: Surface, Field (used by tools/projections.py).
    + AGUIEvent gained "CUSTOM" and "USER_ACTION" event names.
    + New inbound user-action shape from the dashboard / A2UI client:
      UserAction (mirrors A2UI v0.8 userAction message).
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field

CONTRACTS_VERSION = "0.0.2"

# ============================================================================
# §6.1  Client -> Backend
# ============================================================================


class VoiceQuery(BaseModel):
    type: Literal["voice_query"] = "voice_query"
    session_id: str
    text: str


class Command(BaseModel):
    type: Literal["command"] = "command"
    session_id: str
    action: Literal["load_dataset", "train_baseline", "run_evals", "reset"]
    params: dict[str, Any] = Field(default_factory=dict)


# Recognised interaction actions. Open string is allowed but these are the
# ones with explicit server-side handlers (PLAN §6.1 + A2UI phase).
INTERACTION_ACTIONS = (
    "select_panel",
    "select_point",
    "grab_region",
    "confirm_transform",
    "dismiss",
    "stop_training",
    "keep_training",
)


class Interaction(BaseModel):
    """VR/dashboard interactions. action is intentionally an open string.

    Common shapes:
        select_panel        : target_id="<panel_id>"
        select_point        : target_id="<point_id>"  OR  point_ids=[...]
        grab_region         : point_ids=[...]
        confirm_transform   : context={"column": str, "transform": "log"}
        dismiss             : context={"column": str}
        stop_training       : context={"run_id": str, "step": int, "verdict": str}
        keep_training       : context={"run_id": str, "step": int, "verdict": str}
    """
    type: Literal["interaction"] = "interaction"
    session_id: str
    action: str
    target_id: str | None = None
    point_ids: list[str] | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class UserAction(BaseModel):
    """A2UI v0.8 `userAction` mirror — arrives from the dashboard when a user
    taps a button inside an A2UI surface. We accept it on /ws too so the VR
    client can synthesize button clicks without a second back-channel.
    """
    type: Literal["user_action"] = "user_action"
    session_id: str
    surface_id: str
    action: str
    context: dict[str, Any] = Field(default_factory=dict)


ClientMessage = Union[VoiceQuery, Command, Interaction, UserAction]


# ============================================================================
# §6.2  Backend -> Client
# ============================================================================


class Speech(BaseModel):
    type: Literal["speech"] = "speech"
    agent: str
    text: str


PanelKind = Literal["histogram", "box", "kde", "corr", "missing"]
PositionHint = Literal["left", "right", "center", "above", "below"]


class Panel(BaseModel):
    id: str
    kind: PanelKind
    title: str
    column: str | None = None
    image_b64: str
    position_hint: PositionHint = "center"
    flags: list[str] = Field(default_factory=list)


class Panels(BaseModel):
    type: Literal["panels"] = "panels"
    panels: list[Panel]


class ScatterAxes(BaseModel):
    x: str
    y: str
    z: str


class ScatterPoint(BaseModel):
    id: str
    x: float
    y: float
    z: float
    color: str = "#3cb371"
    size: float = 0.04
    shape: Literal["sphere", "cube", "tetra"] = "sphere"
    label: str | None = None


class Scatter3D(BaseModel):
    type: Literal["scatter3d"] = "scatter3d"
    title: str
    axes: ScatterAxes
    points: list[ScatterPoint]


class TrainingUpdate(BaseModel):
    type: Literal["training_update"] = "training_update"
    run_id: str
    step: int
    metrics: dict[str, float]
    status: Literal["running", "stopped", "done"] = "running"


class Highlight(BaseModel):
    type: Literal["highlight"] = "highlight"
    target_ids: list[str]
    reason: str


class ReportSection(BaseModel):
    title: str
    body: str


class Report(BaseModel):
    type: Literal["report"] = "report"
    speak: bool = True
    verdict: str
    sections: list[ReportSection] = Field(default_factory=list)


class Surface(BaseModel):
    """KDE / density surface payload for the VR client (PLAN §6.2 extension).

    z is row-major: z[row][col] where row indexes y_extent, col indexes x_extent.
    """
    type: Literal["surface"] = "surface"
    title: str
    axes: ScatterAxes  # {x, y, z}; z label is typically 'density'
    grid: int
    x_extent: list[float]   # [xmin, xmax]
    y_extent: list[float]
    z: list[list[float]]    # shape (grid, grid)


class CorrField(BaseModel):
    """Generic 2D field (e.g. correlation matrix) for the VR client.

    Wire `type` is "field"; the class is named CorrField to avoid shadowing
    pydantic.Field.
    """
    type: Literal["field"] = "field"
    title: str
    labels: list[str]
    values: list[list[float]]
    range: list[float] = Field(default_factory=lambda: [-1.0, 1.0])


AgentState = Literal["thinking", "tool_call", "handoff", "done", "error"]


class AgentStatus(BaseModel):
    type: Literal["agent_status"] = "agent_status"
    agent: str
    state: AgentState
    message: str | None = None


ServerMessage = Union[
    Speech, Panels, Scatter3D, Surface, CorrField,
    TrainingUpdate, Highlight, Report, AgentStatus,
]


# ============================================================================
# §6.3  AG-UI events (backend -> spectator dashboard)
# ============================================================================

AGUIEventName = Literal[
    "RUN_STARTED",
    "TEXT_MESSAGE_CONTENT",
    "TOOL_CALL_START",
    "TOOL_CALL_END",
    "STATE_DELTA",
    "HANDOFF",
    "RUN_FINISHED",
    "CUSTOM",       # generic extension hook; A2UI envelopes ride here
    "USER_ACTION",  # client → server replay of an A2UI userAction
]


class AGUIEvent(BaseModel):
    """AG-UI protocol envelope (CopilotKit-compatible).

    For A2UI: event="CUSTOM", `name` = A2UI envelope key
    ('surfaceUpdate' | 'dataModelUpdate' | 'beginRendering' for v0.8;
     'createSurface' | 'updateComponents' | 'updateDataModel' for v0.9),
    `value` = the A2UI envelope JSON.
    """
    event: AGUIEventName
    name: str | None = None           # CUSTOM event sub-type (e.g. 'surfaceUpdate')
    agent: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: Any | None = None
    value: Any | None = None          # CUSTOM payload
    ts: float                          # unix seconds


# ============================================================================
# §6.4  Internal: EDA per-column result
# ============================================================================


class EDAColumnResult(BaseModel):
    column: str
    dtype: str
    missing_pct: float
    skew: float | None = None
    outlier_pct: float | None = None
    flags: list[str] = Field(default_factory=list)
    plot: PanelKind | None = None
    note: str | None = None
