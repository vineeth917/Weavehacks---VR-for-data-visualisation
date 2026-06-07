"""Single context type that flows through the router and every specialist
agent in the Agents-SDK handoff graph.

Router → EDA → (handoffs need the SAME context type), so we widen rather than
maintain a parallel hierarchy. EDA tools use `df` + `dataset_name`; training
tools use `active_run_id`. Both are optional and lazily filled by the
orchestrator before the agent runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class OrchestratorContext:
    session_id: str
    dataset_name: str = "titanic"
    df: pd.DataFrame | None = None
    active_run_id: str | None = None
    scratch: dict[str, Any] = field(default_factory=dict)
