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

import io
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError
from sse_starlette.sse import EventSourceResponse

from backend import config
from backend.a2ui import ACTIONS as A2UI_ACTIONS
from backend.a2ui import emitter as a2ui_emitter
from backend.a2ui import surfaces as a2ui_surfaces
from backend.agents import dataset_registry
from backend.agents import eda as eda_agent
from backend.agents import narrator as narrator_agent
from backend.agents import router as router_agent
from backend.agents import run_registry
from backend.agents.context import OrchestratorContext
from backend.contracts import (
    CONTRACTS_VERSION,
    AGUIEvent,
    AgentStatus,
    ClientMessage,
    Command,
    Highlight,
    Interaction,
    Panel,
    Panels,
    Report,
    ReportSection,
    Speech,
    TrainingUpdate,
    UserAction,
    VoiceQuery,
)
from backend.tools import redis_state
from backend.tools.plots import build_panel

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
# /dev-ui — throwaway local test UI (backend/scripts/test_ui/index.html)
# Same origin so no CORS or proxy required. Safe to ship; pure static HTML.
# ---------------------------------------------------------------------------
_DEV_UI_DIR = Path(__file__).resolve().parent / "scripts" / "test_ui"
if _DEV_UI_DIR.exists():
    app.mount("/dev-ui", StaticFiles(directory=str(_DEV_UI_DIR), html=True),
              name="dev-ui")
    log.info("dev UI mounted at /dev-ui (from %s)", _DEV_UI_DIR)


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
# /transcribe  —  audio -> text via OpenAI Whisper
#
# Accepts any audio container Whisper supports (webm/opus, wav, mp3, m4a, ogg).
# Returns {"text": str, "model": str, "latency_ms": int}. Up to 25 MB per
# request — that's Whisper's hard limit, not ours.
#
# The dev UI uses this for the "hold-to-talk" button; Person B's WebXR client
# will use the same endpoint from the headset (record a Blob → POST here →
# send the returned text back over /ws as a normal voice_query).
# ---------------------------------------------------------------------------

_WHISPER_MODEL = "whisper-1"
_WHISPER_MAX_BYTES = 25 * 1024 * 1024


# ---------------------------------------------------------------------------
# /action  —  A2UI v0.9 client → server action receiver
#
# v0.9 dispatch shape (per https://a2ui.org/concepts/actions/):
#   {"version":"v0.9","action":{"name":"confirm_transform",
#                               "surfaceId":"eda-action",
#                               "sourceComponentId":"confirm",
#                               "timestamp":"2026-...",
#                               "context":{"column":"fare","transform":"log"}}}
#
# We also accept the v0.8 mirror `{"userAction":{...}}` for the same body,
# and a "flat" shape `{"action":"...","context":{...},"surface_id":"..."}`
# the VR client / dashboard JS may use directly. Person C's renderer can
# point at this endpoint regardless of which BUTTON_MODE we chose.
#
# Requires `session_id` either in the JSON body or as ?session_id= query.
# Returns the resolved Interaction for round-trip debugging.
# ---------------------------------------------------------------------------

@app.post("/action")
async def action_post(payload: dict[str, Any]) -> dict[str, Any]:
    sid = payload.get("session_id")
    body = payload.get("action") or payload.get("userAction") or payload
    if isinstance(body, dict):
        name = body.get("name") or body.get("action")
        surface_id = body.get("surfaceId") or body.get("surface_id") \
                      or body.get("sourceComponentId")
        context = body.get("context") or {}
        sid = sid or body.get("session_id")
    else:
        raise HTTPException(status_code=400, detail="action body must be an object")
    if not sid:
        raise HTTPException(status_code=400, detail="missing session_id")
    if not name:
        raise HTTPException(status_code=400, detail="missing action name")

    iact = Interaction(
        session_id=str(sid), action=str(name),
        target_id=(str(surface_id) if surface_id else None),
        context=(context if isinstance(context, dict) else {}),
    )
    # Emit a USER_ACTION on /agui so the dashboard / debugger sees the click
    # without us needing a websocket round-trip.
    a2ui_emitter.emit_agui(
        "USER_ACTION",
        {"action": iact.action, "context": iact.context,
         "target_id": iact.target_id, "session_id": sid, "via": "POST /action"},
        agent="router",
    )
    redis_state.push_memory(sid, {"role": "action", "action": iact.action,
                                   "context": iact.context, "via": "POST",
                                   "ts": time.time()})
    return {"ok": True, "interaction": iact.model_dump()}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)) -> dict[str, Any]:
    if not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503,
                            detail="OPENAI_API_KEY not set on the backend")
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio body")
    if len(audio_bytes) > _WHISPER_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"audio too large ({len(audio_bytes)} bytes; max 25 MB)",
        )

    # OpenAI SDK is sync — run in a thread so we don't block the event loop.
    def _do_stt() -> tuple[str, float]:
        from openai import OpenAI
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        bio = io.BytesIO(audio_bytes)
        bio.name = file.filename or "audio.webm"
        t0 = time.time()
        tr = client.audio.transcriptions.create(model=_WHISPER_MODEL, file=bio)
        return tr.text, (time.time() - t0)

    try:
        text, dt = await asyncio.to_thread(_do_stt)
    except Exception as e:  # noqa: BLE001
        log.exception("whisper failed")
        raise HTTPException(status_code=502, detail=f"whisper: {e}") from e

    log.info("transcribe bytes=%d text_len=%d latency_ms=%d",
             len(audio_bytes), len(text), int(dt * 1000))
    return {"text": text, "model": _WHISPER_MODEL, "latency_ms": int(dt * 1000)}


# ---------------------------------------------------------------------------
# /ws  —  VR client transport
# ---------------------------------------------------------------------------

_ClientMessage = TypeAdapter(ClientMessage)


async def _build_and_send_panels(
    ws: WebSocket,
    df,
    panel_specs: list,
    *,
    agent_name: str = "eda",
) -> int:
    """Render PanelSpecs to real Panel PNGs and send a `panels` frame.

    Returns the number of panels actually sent (some specs may fail to render).
    """
    rendered: list[Panel] = []
    for spec in panel_specs[:4]:  # cap at 4 per turn
        spec_d = spec.model_dump() if hasattr(spec, "model_dump") else dict(spec)
        column = spec_d.get("column")
        kind = spec_d.get("kind")
        if kind in (None,):
            continue
        try:
            panel_d = await asyncio.to_thread(
                build_panel, df, column, kind,
                title=spec_d.get("title"),
                flags=spec_d.get("flags") or [],
                position_hint=spec_d.get("position_hint") or "center",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("panel render failed kind=%s col=%s err=%s", kind, column, e)
            continue
        panel_clean = {k: v for k, v in panel_d.items() if not k.startswith("_")}
        rendered.append(Panel.model_validate(panel_clean))

    if rendered:
        await ws.send_json(Panels(panels=rendered).model_dump())
    return len(rendered)


def _emit_findings_surface(findings: list, sid: str, agent_name: str = "eda") -> None:
    """Push the eda-findings A2UI surface (always, even when findings empty)."""
    rows = []
    for f in findings[:10]:
        d = f.model_dump() if hasattr(f, "model_dump") else dict(f)
        rows.append({"column": d.get("column", ""),
                     "flag": d.get("flag", ""),
                     "note": d.get("note", "")})
    if not rows:
        rows = [{"column": "—", "flag": "no flags", "note": "nothing notable"}]
    comps, data, sfc = a2ui_surfaces.eda_findings(rows)
    a2ui_emitter.emit_surface(sfc, comps, data, agent=agent_name)
    # mirror to redis per §6.5
    redis_state.append_findings(sid, rows)


def _emit_action_surface(suggestion, sid: str, agent_name: str = "eda") -> None:
    """Push the eda-action confirm card. Falls back to a generic suggestion."""
    if suggestion is None:
        column, transform = "fare", "log"
    else:
        d = suggestion.model_dump() if hasattr(suggestion, "model_dump") else dict(suggestion)
        column = d.get("column") or "fare"
        transform = d.get("transform") or "log"
    comps, data, sfc = a2ui_surfaces.eda_action(column=column, transform=transform)
    a2ui_emitter.emit_surface(sfc, comps, data, agent=agent_name)


async def _handle_interaction(ws: WebSocket, msg: Interaction) -> None:
    action = msg.action
    sid = msg.session_id

    # ---- 1. A2UI button actions (frozen exact strings) ----
    if action in A2UI_ACTIONS:
        await ws.send_json(
            AgentStatus(agent="router", state="done",
                        message=f"a2ui action: {action} {msg.context}").model_dump()
        )
        a2ui_emitter.emit_agui("USER_ACTION",
                               {"action": action, "context": msg.context,
                                "target_id": msg.target_id},
                               agent="router")
        redis_state.push_memory(sid, {"role": "action", "action": action,
                                       "context": msg.context, "ts": time.time()})
        return

    # ---- 2. spatial selections → real EDA agent ----
    if action in ("select_point", "select_panel", "grab_region"):
        targets = msg.point_ids or ([msg.target_id] if msg.target_id else [])
        await ws.send_json(
            AgentStatus(agent="eda", state="thinking",
                        message=f"{action} on {len(targets)} target(s)").model_dump()
        )

        dataset_name, df = dataset_registry.get_or_default(sid)
        try:
            out = await eda_agent.run_for_interaction(
                sid=sid, action=action,
                point_ids=msg.point_ids, target_id=msg.target_id,
                df=df, dataset_name=dataset_name,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("eda interaction agent failed")
            await ws.send_json(
                AgentStatus(agent="eda", state="error",
                            message=f"agent error: {e}").model_dump()
            )
            return

        await ws.send_json(Speech(agent="eda", text=out.speech).model_dump())
        await ws.send_json(
            Highlight(target_ids=targets,
                      reason=action).model_dump()
        )
        # render any panels the agent asked for
        await _build_and_send_panels(ws, df, out.panel_specs, agent_name="eda")
        # always emit eda-action so the dashboard shows a card; use the agent's
        # transform_suggestion when present, else fall back to a default.
        _emit_action_surface(out.transform_suggestion, sid)
        # mirror findings if any
        if out.findings:
            _emit_findings_surface(out.findings, sid)

        await ws.send_json(AgentStatus(agent="eda", state="done").model_dump())
        return

    # ---- 3. unknown action ----
    await ws.send_json(
        AgentStatus(agent="router", state="error",
                    message=f"unknown interaction action: {action}").model_dump()
    )


async def _send_narrator_report(ws: WebSocket, out, sid: str) -> None:
    """Send a Report frame on /ws + cache the narrative in Redis scratch.

    `out` is a NarratorOutput (Pydantic). We don't emit a dedicated A2UI
    surface here — the Report frame itself is the deliverable, and the dev UI
    already renders it inline. Dashboard can subscribe to /ws or render via
    Person C's preferred path later.
    """
    sections = [
        ReportSection(title=str(s.title), body=str(s.body))
        for s in (out.sections or [])
    ]
    report = Report(
        speak=True,
        verdict=str(out.verdict or "mixed"),
        sections=sections,
    )
    await ws.send_json(report.model_dump())
    # Mirror the spoken summary as a Speech frame so STT-driven flows feel natural.
    if out.speech:
        await ws.send_json(Speech(agent="narrator", text=out.speech).model_dump())
    # Cache the latest narrative under scratch so the dashboard can refetch.
    redis_state.set_scratch(sid, "narrator_last", {
        "speech": out.speech,
        "verdict": out.verdict,
        "sections": [s.model_dump() for s in sections],
        "ts": time.time(),
    })


def _emit_training_verdict_surface(out, sid: str) -> None:
    """Build + emit the training-verdict A2UI surface from a TrainingMonitorOutput."""
    reason = " ".join(out.rationale[:3]) if out.rationale else out.speech
    # truncate reason for the card
    reason = reason[:240]
    comps, data, sfc = a2ui_surfaces.training_verdict(
        run_id=out.run_id or "unknown",
        verdict=out.verdict,
        reason=reason,
        step=int(out.step or 0),
    )
    a2ui_emitter.emit_surface(sfc, comps, data, agent="training_monitor")


async def _handle_voice_query(ws: WebSocket, msg: VoiceQuery) -> None:
    sid = msg.session_id
    await ws.send_json(
        AgentStatus(agent="router", state="thinking",
                    message=f"received: {msg.text!r}").model_dump()
    )
    redis_state.push_memory(sid, {"role": "user", "text": msg.text, "ts": time.time()})

    dataset_name, df = dataset_registry.get_or_default(sid)
    active_run = run_registry.get_active(sid)
    ctx = OrchestratorContext(
        session_id=sid,
        dataset_name=dataset_name,
        df=df,
        active_run_id=active_run,
    )

    try:
        result = await router_agent.route(sid=sid, text=msg.text, ctx=ctx)
    except Exception as e:  # noqa: BLE001
        log.exception("router failed")
        await ws.send_json(
            AgentStatus(agent="router", state="error",
                        message=f"router error: {e}").model_dump()
        )
        return

    # ---- specialist: EDA ----
    if result.target == "eda" and result.eda is not None:
        out = result.eda
        await ws.send_json(Speech(agent="eda", text=out.speech).model_dump())
        n_panels = await _build_and_send_panels(ws, df, out.panel_specs, agent_name="eda")
        _emit_findings_surface(out.findings, sid)
        log.info("voice_query sid=%s -> eda  panels=%d findings=%d ds=%s",
                 sid, n_panels, len(out.findings), dataset_name)
        await ws.send_json(
            AgentStatus(agent="eda", state="done",
                        message=f"panels={n_panels} findings={len(out.findings)} ds={dataset_name}").model_dump()
        )
        return

    # ---- specialist: narrator ----
    if result.target == "narrator" and result.narrator is not None:
        out = result.narrator
        await _send_narrator_report(ws, out, sid)
        log.info("voice_query sid=%s -> narrator verdict=%s sections=%d",
                 sid, out.verdict, len(out.sections))
        await ws.send_json(
            AgentStatus(agent="narrator", state="done",
                        message=f"verdict={out.verdict} sections={len(out.sections)}").model_dump()
        )
        return

    # ---- specialist: training_monitor ----
    if result.target == "training_monitor" and result.training is not None:
        out = result.training
        await ws.send_json(Speech(agent="training_monitor", text=out.speech).model_dump())
        # one training_update frame for the VR client (PLAN §6.2)
        await ws.send_json(TrainingUpdate(
            run_id=out.run_id or active_run,
            step=int(out.step or 0),
            metrics={"verdict_code": {"healthy": 0, "overfitting": 1,
                                       "underfitting": 2, "leakage": 3,
                                       "unknown": -1}.get(out.verdict, -1)},
            status="running",
        ).model_dump())
        _emit_training_verdict_surface(out, sid)
        log.info("voice_query sid=%s -> training_monitor verdict=%s run=%s step=%d",
                 sid, out.verdict, out.run_id, out.step)
        await ws.send_json(
            AgentStatus(agent="training_monitor", state="done",
                        message=f"verdict={out.verdict} action={out.suggested_action} run={out.run_id}").model_dump()
        )
        return

    # ---- nothing usable ----
    await ws.send_json(
        AgentStatus(agent="router", state="error",
                    message=f"router produced no usable output (target={result.target})").model_dump()
    )


async def _handle_command(ws: WebSocket, msg: Command) -> None:
    sid = msg.session_id
    if msg.action == "load_dataset":
        name = (msg.params or {}).get("name", dataset_registry.DEFAULT)
        try:
            df = await asyncio.to_thread(dataset_registry.load, sid, name)
        except Exception as e:  # noqa: BLE001
            await ws.send_json(
                AgentStatus(agent="router", state="error",
                            message=f"load_dataset {name}: {e}").model_dump()
            )
            return
        await ws.send_json(
            AgentStatus(agent="router", state="done",
                        message=f"dataset loaded: {name} shape={df.shape}").model_dump()
        )
        return

    if msg.action == "narrate":
        await ws.send_json(
            AgentStatus(agent="narrator", state="thinking",
                        message="rolling up session state").model_dump()
        )
        try:
            out = await narrator_agent.run_narrator(sid, text=(msg.params or {}).get("text"))
        except Exception as e:  # noqa: BLE001
            log.exception("narrator failed")
            await ws.send_json(
                AgentStatus(agent="narrator", state="error",
                            message=f"narrator: {e}").model_dump()
            )
            return
        await _send_narrator_report(ws, out, sid)
        await ws.send_json(
            AgentStatus(agent="narrator", state="done",
                        message=f"verdict={out.verdict} sections={len(out.sections)}").model_dump()
        )
        return

    if msg.action == "load_run":
        run_id = (msg.params or {}).get("run_id", run_registry.DEFAULT_RUN)
        run_registry.set_active(sid, run_id)
        await ws.send_json(
            AgentStatus(agent="router", state="done",
                        message=f"active run set: {run_id}").model_dump()
        )
        return

    if msg.action == "reset":
        n = redis_state.reset_session(sid)
        dataset_registry.reset(sid)
        run_registry.reset(sid)
        log.info("reset session=%s purged_keys=%d", sid, n)

    await ws.send_json(
        AgentStatus(agent="router", state="done",
                    message=f"command acknowledged: {msg.action}").model_dump()
    )


async def _handle_message(ws: WebSocket, msg: ClientMessage) -> None:
    if isinstance(msg, VoiceQuery):
        await _handle_voice_query(ws, msg)

    elif isinstance(msg, Command):
        await _handle_command(ws, msg)

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
