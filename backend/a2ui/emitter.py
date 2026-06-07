"""A2UI → AG-UI CUSTOM event emitter.

Each A2UI envelope (surfaceUpdate / dataModelUpdate / beginRendering) is wrapped
in an AG-UI `CUSTOM` event whose `name` is the envelope key. Events are pushed
into an asyncio.Queue and broadcast over the /agui SSE stream.

Wire format (one SSE message per event):

    event: CUSTOM
    data: {"type":"CUSTOM","name":"surfaceUpdate","value":{...envelope...},"ts":...}
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from backend.a2ui import DEFAULT_CATALOG, SPEC_VERSION
from backend.a2ui.values import encode_contents

log = logging.getLogger("hololab.a2ui")

# ---------------------------------------------------------------------------
# Global broadcast bus — every connected /agui SSE client gets a Queue.
# ---------------------------------------------------------------------------

_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
# Last N events replayed to every new /agui subscriber so a client that
# connects after a voice_query still sees HANDOFF + A2UI surfaces.
_REPLAY: deque[dict[str, Any]] = deque(maxlen=64)


def subscribe() -> asyncio.Queue[dict[str, Any]]:
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue[dict[str, Any]]) -> None:
    _subscribers.discard(q)


def replay_buffer() -> list[dict[str, Any]]:
    """Snapshot of recent AG-UI events for new SSE subscribers."""
    return list(_REPLAY)


def _broadcast(event: dict[str, Any]) -> None:
    """Fan out to every subscriber. Drops on full queue."""
    _REPLAY.append(event)
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("agui subscriber queue full — dropping")


# ---------------------------------------------------------------------------
# AG-UI helpers
# ---------------------------------------------------------------------------

def emit_agui(name: str, value: Any, *, agent: str | None = None,
              tool: str | None = None) -> None:
    """Emit a generic AG-UI event (RUN_STARTED, TOOL_CALL_*, HANDOFF, etc.).

    For A2UI envelopes use emit_surface() — it formats the CUSTOM wrapper.
    """
    _broadcast({
        "name": name,
        "value": value,
        "agent": agent,
        "tool": tool,
        "ts": time.time(),
    })


# ---------------------------------------------------------------------------
# A2UI envelope builders (v0.8)
# ---------------------------------------------------------------------------

def _surface_update_envelope(surface_id: str, components: list[dict]) -> dict:
    return {"surfaceUpdate": {"surfaceId": surface_id, "components": components}}


def _data_model_update_envelope(surface_id: str, data: dict) -> dict:
    return {"dataModelUpdate": {"surfaceId": surface_id,
                                "contents": encode_contents(data)}}


def _begin_rendering_envelope(surface_id: str, *,
                              root: str = "root",
                              catalog_id: str = DEFAULT_CATALOG) -> dict:
    return {"beginRendering": {"surfaceId": surface_id,
                               "root": root, "catalogId": catalog_id}}


# v0.9 builders kept for future flip (CopilotKit middleware accepts either).
def _surface_update_envelope_v09(surface_id: str, components: list[dict]) -> dict:
    return {"updateComponents": {"surfaceId": surface_id,
                                 "components": components}}


def _data_model_update_envelope_v09(surface_id: str, data: dict) -> dict:
    return {"updateDataModel": {"surfaceId": surface_id, "value": data}}


def _create_surface_envelope_v09(surface_id: str, *,
                                 catalog_id: str = DEFAULT_CATALOG) -> dict:
    return {"createSurface": {"surfaceId": surface_id, "catalogId": catalog_id}}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def emit_surface(surface_id: str, components: list[dict], data: dict,
                 *, agent: str | None = None) -> None:
    """Emit the full triple for one A2UI surface, ordered per v0.8 spec.

    Order (v0.8):  surfaceUpdate -> dataModelUpdate -> beginRendering.
    Order (v0.9):  createSurface -> updateComponents -> updateDataModel
                   (createSurface is the first message, not last).
    """
    if SPEC_VERSION == "0.8":
        envs = [
            ("surfaceUpdate",    _surface_update_envelope(surface_id, components)),
            ("dataModelUpdate",  _data_model_update_envelope(surface_id, data)),
            ("beginRendering",   _begin_rendering_envelope(surface_id)),
        ]
    else:  # "0.9"
        envs = [
            ("createSurface",    _create_surface_envelope_v09(surface_id)),
            ("updateComponents", _surface_update_envelope_v09(surface_id, components)),
            ("updateDataModel",  _data_model_update_envelope_v09(surface_id, data)),
        ]
    for name, env in envs:
        emit_agui(name, env, agent=agent)
    log.info("a2ui surface emitted id=%s spec=%s frames=%d",
             surface_id, SPEC_VERSION, len(envs))
