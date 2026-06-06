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
from backend.contracts import (
    AGUIEvent,
    AgentStatus,
    ClientMessage,
    Command,
    Interaction,
    Speech,
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
    }


# ---------------------------------------------------------------------------
# /ws  —  VR client transport
# ---------------------------------------------------------------------------

_ClientMessage = TypeAdapter(ClientMessage)


async def _handle_message(ws: WebSocket, msg: ClientMessage) -> None:
    """Phase 0: validate + echo back AgentStatus so B can verify the loop.

    Real agent routing arrives in Phase 3 (router agent).
    """
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
        await ws.send_json(
            AgentStatus(agent="router", state="done",
                        message=f"interaction {msg.action}:{msg.target_id}").model_dump()
        )


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
    """Phase-0 heartbeat stream so C can wire the SSE subscription early."""

    async def gen() -> AsyncIterator[dict[str, Any]]:
        seq = 0
        while True:
            ev = AGUIEvent(
                event="STATE_DELTA",
                agent="system",
                args={"heartbeat": seq},
                ts=time.time(),
            )
            yield {"event": ev.event, "data": ev.model_dump_json()}
            seq += 1
            await asyncio.sleep(5)

    return EventSourceResponse(gen())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host=config.HOST, port=config.PORT, reload=False)
