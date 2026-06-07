"""Per-session active W&B run id.

Mirrors `dataset_registry` for the training-monitor path. The replay file
ships three demo runs; the default is the overfitting one (most instructive
for a hackathon demo).
"""
from __future__ import annotations

import logging

log = logging.getLogger("hololab.runs")

DEFAULT_RUN = "demo-overfit-001"
KNOWN = ("demo-overfit-001", "demo-healthy-002", "demo-leakage-003")

_active: dict[str, str] = {}


def set_active(sid: str, run_id: str) -> None:
    _active[sid] = run_id
    log.info("active run sid=%s run_id=%s", sid, run_id)


def get_active(sid: str) -> str:
    return _active.get(sid, DEFAULT_RUN)


def reset(sid: str) -> None:
    _active.pop(sid, None)
