"""HoloLab backend — FastAPI app.

Endpoints:
    GET  /healthz   liveness + dependency probe
    WS   /ws        VR client <-> backend (PLAN §6.1/§6.2)
    GET  /agui      spectator dashboard SSE stream (PLAN §6.3, stub in Phase 0)

Phase 0 scope:
    - Boot uvicorn
    - WebSocket echo with contract validation (parses ClientMessage; replies
      with AgentStatus("done") so B can verify round-trip)
    - Redis ping in /healthz
    - Optional weave.init() — failures don't crash the app
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import TypeAdapter, ValidationError
from sse_starlette.sse import EventSourceResponse

from backend import config
from backend.a2ui import ACTIONS as A2UI_ACTIONS
from backend.a2ui import emitter as a2ui_emitter
from backend.a2ui import surfaces as a2ui_surfaces
from backend.contracts import (
    CONTRACTS_VERSION,
    AGUIEvent,
    AgentStatus,
    ClientMessage,
    Command,
    Highlight,
    Interaction,
    Speech,
    UserAction,
    VoiceQuery,
)
from backend.tools import redis_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("hololab")

# ---------------------------------------------------------------------------
# weave init (best-effort)
# ---------------------------------------------------------------------------

_weave_ok = False


def _init_weave() -> None:
    """Init Weave; tolerate failures so dev can iterate without W&B network."""
    global _weave_ok
    if not config.USE_WEAVE:
        log.warning("USE_WEAVE=0 — skipping weave.init()")
        return
    try:
        import weave  # type: ignore

        weave.init(config.weave_project_full())
        _weave_ok = True
        log.info("weave.init OK project=%s", config.weave_project_full())
    except Exception as e:  # noqa: BLE001
        log.warning("weave.init failed (continuing without trace): %s", e)


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config.assert_keys()
    _init_weave()
    if not redis_state.ping():
        log.warning("Redis ping failed at startup (REDIS_URL=%s)", config.REDIS_URL)
    else:
        log.info("Redis OK (%s)", config.REDIS_URL)
    log.info(
        "Models: router=%s reasoning=%s deep=%s fallback=%s",
        config.ROUTER_MODEL,
        config.REASONING_MODEL,
        config.DEEP_REASONING_MODEL,
        config.FALLBACK_MODEL,
    )
    yield
    log.info("shutdown")


app = FastAPI(title="hololab-backend", version="0.0.1", lifespan=lifespan)

# CORS — Person C's dashboard + Person B's WebXR client need to hit us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "redis": redis_state.ping(),
        "weave": _weave_ok,
        "models": {
            "router": config.ROUTER_MODEL,
            "reasoning": config.REASONING_MODEL,
            "deep": config.DEEP_REASONING_MODEL,
            "fallback": config.FALLBACK_MODEL,
        },
        "wandb_inference_base": config.WANDB_INFERENCE_BASE,
        "version": app.version,
        "contracts_version": CONTRACTS_VERSION,
        "a2ui_actions": sorted(A2UI_ACTIONS),
    }


# ---------------------------------------------------------------------------
# /ws  —  VR client transport
# ---------------------------------------------------------------------------

_ClientMessage = TypeAdapter(ClientMessage)


async def _handle_interaction(ws: WebSocket, msg: Interaction) -> None:
    """Route an interaction to the right behaviour.

    Phase: interaction-loop + A2UI (pre-agent shim).
    Real EDA-agent invocation arrives in the next phase; here we exercise the
    full transport so B & C can integrate.
    """
    action = msg.action
    sid = msg.session_id

    # ---- 1. recognised A2UI button actions (exact strings) ----
    if action in A2UI_ACTIONS:
        # acknowledge on /ws
        await ws.send_json(
            AgentStatus(agent="router", state="done",
                        message=f"a2ui action: {action} {msg.context}").model_dump()
        )
        # mirror back to /agui dashboard
        a2ui_emitter.emit_agui("USER_ACTION",
                               {"action": action, "context": msg.context,
                                "target_id": msg.target_id},
                               agent="router")
        redis_state.push_memory(sid, {"role": "action", "action": action,
                                       "context": msg.context, "ts": time.time()})
        return

    # ---- 2. select_point / grab_region: ask EDA to comment + highlight ----
    if action in ("select_point", "select_panel", "grab_region"):
        targets = msg.point_ids or ([msg.target_id] if msg.target_id else [])
        await ws.send_json(
            AgentStatus(agent="eda", state="thinking",
                        message=f"{action} on {len(targets)} target(s)").model_dump()
        )
        # Speech + Highlight stub (real LLM call arrives with agents/eda.py).
        await ws.send_json(
            Speech(agent="eda",
                   text=(f"You selected {len(targets)} point(s). "
                         "I'll inspect those rows.") if action != "select_panel"
                   else f"Inspecting panel {msg.target_id}.").model_dump()
        )
        await ws.send_json(
            Highlight(target_ids=targets,
                      reason=f"{action} ack").model_dump()
        )
        # Trigger the eda-action surface so the dashboard renders a confirm card
        # (real agent will choose the column from the selection; we pick the
        # first target's column suffix as a placeholder).
        column = (targets[0].split("_", 1)[0] if targets else "price")
        comps, data, sfc = a2ui_surfaces.eda_action(column=column, transform="log")
        a2ui_emitter.emit_surface(sfc, comps, data, agent="eda")

        await ws.send_json(
            AgentStatus(agent="eda", state="done").model_dump()
        )
        return

    # ---- 3. unknown action ----
    await ws.send_json(
        AgentStatus(agent="router", state="error",
                    message=f"unknown interaction action: {action}").model_dump()
    )


async def _handle_message(ws: WebSocket, msg: ClientMessage) -> None:
    if isinstance(msg, VoiceQuery):
        await ws.send_json(
            AgentStatus(agent="router", state="thinking",
                        message=f"received voice_query: {msg.text!r}").model_dump()
        )
        await ws.send_json(
            Speech(agent="router",
                   text=f"(echo) You said: {msg.text}").model_dump()
        )
        await ws.send_json(
            AgentStatus(agent="router", state="done").model_dump()
        )
        redis_state.push_memory(msg.session_id,
                                {"role": "user", "text": msg.text, "ts": time.time()})

    elif isinstance(msg, Command):
        await ws.send_json(
            AgentStatus(agent="router", state="done",
                        message=f"command acknowledged: {msg.action}").model_dump()
        )
        if msg.action == "reset":
            n = redis_state.reset_session(msg.session_id)
            log.info("reset session=%s purged_keys=%d", msg.session_id, n)

    elif isinstance(msg, Interaction):
        await _handle_interaction(ws, msg)

    elif isinstance(msg, UserAction):
        # A2UI userAction replay on /ws — normalise to an Interaction.
        iact = Interaction(session_id=msg.session_id, action=msg.action,
                           target_id=msg.surface_id, context=msg.context)
        await _handle_interaction(ws, iact)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    peer = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
    log.info("ws connected peer=%s", peer)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
                msg = _ClientMessage.validate_python(payload)
            except (json.JSONDecodeError, ValidationError) as e:
                await ws.send_json(
                    AgentStatus(agent="router", state="error",
                                message=f"bad message: {e}").model_dump()
                )
                continue
            await _handle_message(ws, msg)
    except WebSocketDisconnect:
        log.info("ws disconnected peer=%s", peer)


# ---------------------------------------------------------------------------
# /agui  —  AG-UI SSE stub (Phase 7 will wire real events)
# ---------------------------------------------------------------------------


@app.get("/agui")
async def agui_stream() -> EventSourceResponse:
    """SSE stream of AG-UI events (incl. A2UI CUSTOM events).

    Each connected client gets its own asyncio.Queue subscription via
    a2ui_emitter.subscribe(). Backend code anywhere can call
    a2ui_emitter.emit_agui(...) / emit_surface(...) and it fans out here.
    """
    q = a2ui_emitter.subscribe()

    # First-class AG-UI event names — anything else gets wrapped in CUSTOM.
    FIRST_CLASS = {
        "RUN_STARTED", "RUN_FINISHED",
        "TEXT_MESSAGE_CONTENT",
        "TOOL_CALL_START", "TOOL_CALL_END",
        "STATE_DELTA", "HANDOFF",
        "USER_ACTION",
    }

    async def gen() -> AsyncIterator[dict[str, Any]]:
        try:
            initial = AGUIEvent(event="STATE_DELTA", agent="system",
                                args={"hello": True,
                                      "contracts_version": CONTRACTS_VERSION},
                                ts=time.time())
            yield {"event": initial.event, "data": initial.model_dump_json()}

            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    hb = AGUIEvent(event="STATE_DELTA", agent="system",
                                   args={"heartbeat": True}, ts=time.time())
                    yield {"event": hb.event, "data": hb.model_dump_json()}
                    continue

                name = item.get("name")
                if name in FIRST_CLASS:
                    ev = AGUIEvent(
                        event=name,                               # type: ignore[arg-type]
                        agent=item.get("agent"),
                        tool=item.get("tool"),
                        args=item.get("value"),
                        ts=item.get("ts", time.time()),
                    )
                else:
                    # A2UI envelope or any other sub-typed payload
                    ev = AGUIEvent(
                        event="CUSTOM",
                        name=name,
                        agent=item.get("agent"),
                        tool=item.get("tool"),
                        value=item.get("value"),
                        ts=item.get("ts", time.time()),
                    )
                yield {"event": ev.event, "data": ev.model_dump_json()}
        finally:
            a2ui_emitter.unsubscribe(q)

    return EventSourceResponse(gen())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host=config.HOST, port=config.PORT, reload=False)
