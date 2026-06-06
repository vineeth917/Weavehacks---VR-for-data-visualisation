"""Wire-level message schemas (PLAN.md §6).

Frozen. Any change here is a stop-and-broadcast event for B & C.

All inbound messages carry a discriminator `type` and a `session_id`.
All outbound messages carry a discriminator `type`.
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field

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


class Interaction(BaseModel):
    type: Literal["interaction"] = "interaction"
    session_id: str
    action: Literal["select_panel", "select_point"]
    target_id: str


ClientMessage = Union[VoiceQuery, Command, Interaction]


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


AgentState = Literal["thinking", "tool_call", "handoff", "done", "error"]


class AgentStatus(BaseModel):
    type: Literal["agent_status"] = "agent_status"
    agent: str
    state: AgentState
    message: str | None = None


ServerMessage = Union[
    Speech, Panels, Scatter3D, TrainingUpdate, Highlight, Report, AgentStatus
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
]


class AGUIEvent(BaseModel):
    """AG-UI protocol envelope (CopilotKit-compatible)."""
    event: AGUIEventName
    agent: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: Any | None = None
    ts: float  # unix seconds


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
