#!/usr/bin/env python3
"""Phase test: interaction-loop + A2UI envelopes + 3D tools.

Runs against a live backend on http://127.0.0.1:8080.

What it asserts:
  T1.  /healthz reports contracts_version=0.0.2 and all 4 A2UI actions
  T2.  ws round-trip: select_point → speech + highlight + agent_status done
       (and triggers an A2UI surface on /agui in parallel)
  T3.  /agui delivers, in order, surfaceUpdate → dataModelUpdate → beginRendering
       for surfaceId="eda-action" with the right v0.8 shape
  T4.  ws round-trip: each of confirm_transform | dismiss | stop_training |
       keep_training round-trips to a USER_ACTION on /agui
  T5.  project_3d / kde_surface / corr_field produce valid contracts payloads

Usage:
  source .venv/bin/activate
  uvicorn backend.main:app --port 8080 &
  python backend/scripts/test_phase_a2ui.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.a2ui import ACTIONS, SURFACE_IDS  # noqa: E402
from backend.contracts import (  # noqa: E402
    CONTRACTS_VERSION,
    CorrField,
    Scatter3D,
    Surface,
)
from backend.tools.projections import (  # noqa: E402
    corr_field,
    kde_surface,
    project_3d,
)

WS_URL = "ws://127.0.0.1:8080/ws"
HEALTH_URL = "http://127.0.0.1:8080/healthz"
AGUI_URL = "http://127.0.0.1:8080/agui"
SID = f"test-{int(time.time())}"

FAILURES: list[str] = []


def _ok(msg: str) -> None: print(f"  PASS  {msg}")
def _bad(msg: str) -> None: print(f"  FAIL  {msg}"); FAILURES.append(msg)


# ---------------------------------------------------------------------------
# T1. /healthz
# ---------------------------------------------------------------------------
def t1_healthz() -> None:
    print("\n=== T1. /healthz ===")
    r = httpx.get(HEALTH_URL, timeout=5.0)
    j = r.json()
    print(json.dumps(j, indent=2))
    (_ok if j.get("contracts_version") == CONTRACTS_VERSION else _bad)(
        f"contracts_version == {CONTRACTS_VERSION}"
    )
    (_ok if set(j.get("a2ui_actions") or []) == ACTIONS else _bad)(
        f"a2ui_actions == frozen set (got {j.get('a2ui_actions')})"
    )


# ---------------------------------------------------------------------------
# T3 helper: read N SSE events from /agui, with a timeout per event.
# ---------------------------------------------------------------------------
async def _sse_collect(n: int, timeout_per_event: float = 5.0,
                       skip_state_delta: bool = True) -> list[dict[str, Any]]:
    """Open /agui as an SSE client and return n parsed events (excluding heartbeats)."""
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", AGUI_URL, headers={"Accept": "text/event-stream"}) as resp:
            assert resp.status_code == 200, f"/agui status {resp.status_code}"
            cur_event = None
            async for line in resp.aiter_lines():
                if not line:
                    cur_event = None
                    continue
                if line.startswith("event:"):
                    cur_event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    if skip_state_delta and payload.get("event") == "STATE_DELTA":
                        continue
                    out.append(payload)
                    if len(out) >= n:
                        return out
                # implicit: continue silently for comments/keepalive
    return out


def _custom_surface_id(payload: dict[str, Any]) -> str | None:
    """Extract surfaceId from a CUSTOM A2UI envelope."""
    val = payload.get("value")
    if not isinstance(val, dict):
        return None
    for key in ("surfaceUpdate", "dataModelUpdate", "beginRendering"):
        inner = val.get(key)
        if isinstance(inner, dict) and inner.get("surfaceId"):
            return inner["surfaceId"]
    return None


async def _sse_collect_surface_triple(surface_id: str, timeout: float = 30.0,
                                    skip_state_delta: bool = True) -> list[dict[str, Any]]:
    """Wait for surfaceUpdate → dataModelUpdate → beginRendering for one surface.

    Skips replay-buffer CUSTOM frames for other surfaces that arrive on connect.
    """
    expected = ["surfaceUpdate", "dataModelUpdate", "beginRendering"]
    matched: list[dict[str, Any]] = []
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", AGUI_URL, headers={"Accept": "text/event-stream"}) as resp:
            assert resp.status_code == 200, f"/agui status {resp.status_code}"
            async for line in resp.aiter_lines():
                if time.time() > deadline:
                    break
                if not line.startswith("data:"):
                    continue
                payload = json.loads(line[len("data:"):].strip())
                if skip_state_delta and payload.get("event") == "STATE_DELTA":
                    continue
                if payload.get("event") != "CUSTOM":
                    continue
                name = payload.get("name")
                if name != expected[len(matched)]:
                    continue
                if _custom_surface_id(payload) != surface_id:
                    continue
                matched.append(payload)
                if len(matched) == 3:
                    return matched
    return matched


async def _sse_wait_for_user_action(action: str, timeout: float = 8.0,
                                    skip_state_delta: bool = True) -> list[dict[str, Any]]:
    """Collect SSE events until a matching USER_ACTION arrives.

    New /agui subscribers receive the replay ring buffer first (HANDOFF +
    A2UI surfaces from earlier in the test). Grabbing only the first event
    therefore returns stale CUSTOM frames — wait for the action we just sent.
    """
    out: list[dict[str, Any]] = []
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", AGUI_URL, headers={"Accept": "text/event-stream"}) as resp:
            assert resp.status_code == 200, f"/agui status {resp.status_code}"
            async for line in resp.aiter_lines():
                if time.time() > deadline:
                    break
                if not line.startswith("data:"):
                    continue
                payload = json.loads(line[len("data:"):].strip())
                if skip_state_delta and payload.get("event") == "STATE_DELTA":
                    continue
                out.append(payload)
                if payload.get("event") == "USER_ACTION":
                    if (payload.get("args") or {}).get("action") == action:
                        return out
                elif (payload.get("event") == "CUSTOM"
                      and payload.get("name") == "USER_ACTION"
                      and (payload.get("value") or {}).get("action") == action):
                    return out
    return out


# ---------------------------------------------------------------------------
# T2 + T3 + T4: ws + sse coordination
# ---------------------------------------------------------------------------
async def t2_t3_select_point_triggers_surface() -> None:
    print("\n=== T2 + T3. select_point on /ws triggers A2UI surface on /agui ===")

    # Subscribe to /agui FIRST so we don't miss the events.
    # Use surface-filtered collect — replay buffer may prepend stale CUSTOM frames.
    sse_task = asyncio.create_task(_sse_collect_surface_triple("eda-action", timeout=30.0))
    await asyncio.sleep(0.6)  # let the subscription register

    ws_frames: list[dict[str, Any]] = []
    async with websockets.connect(WS_URL, open_timeout=5) as ws:
        await ws.send(json.dumps({
            "type": "interaction", "session_id": SID,
            "action": "select_point", "point_ids": ["r12", "r37", "r91"],
        }))
        # the real EDA agent now answers here — wait up to 30s for done
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=8.0))
                ws_frames.append(frame)
                if frame.get("type") == "agent_status" and frame.get("state") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                break

    print("  ws frames:")
    for f in ws_frames:
        print("    ←", json.dumps(f)[:120])

    types = [f["type"] for f in ws_frames]
    (_ok if "speech" in types else _bad)(f"ws got speech (types={types})")
    (_ok if "highlight" in types else _bad)(f"ws got highlight (types={types})")
    (_ok if any(f.get("type") == "agent_status" and f.get("state") == "done"
                for f in ws_frames) else _bad)(
        "ws got agent_status done"
    )

    # /agui side
    sse_events = await asyncio.wait_for(sse_task, timeout=12.0)
    print(f"\n  /agui CUSTOM events (count={len(sse_events)}):")
    for e in sse_events:
        print("    ←", e.get("event"), e.get("name"),
              json.dumps(e.get("value"))[:120] if e.get("value") else "")

    names = [e.get("name") for e in sse_events if e.get("event") == "CUSTOM"]
    expected_order = ["surfaceUpdate", "dataModelUpdate", "beginRendering"]
    (_ok if names[:3] == expected_order else _bad)(
        f"A2UI envelope order: expected {expected_order}, got {names}"
    )

    # T3: shape of each envelope
    if len(sse_events) >= 3 and names[:3] == expected_order:
        su = sse_events[0]["value"]["surfaceUpdate"]
        (_ok if su["surfaceId"] == "eda-action" else _bad)(
            f"surfaceUpdate.surfaceId == 'eda-action' (got {su.get('surfaceId')})"
        )
        (_ok if any(c["id"] == "root" for c in su["components"]) else _bad)(
            "surfaceUpdate.components has id=root"
        )
        (_ok if any("Button" in c["component"] for c in su["components"]) else _bad)(
            "surfaceUpdate.components has a Button"
        )

        dmu = sse_events[1]["value"]["dataModelUpdate"]
        (_ok if dmu["surfaceId"] == "eda-action" else _bad)(
            "dataModelUpdate.surfaceId matches"
        )
        keys = [e.get("key") for e in dmu["contents"]]
        (_ok if "prompt" in keys and "rationale" in keys else _bad)(
            f"dataModelUpdate contents keys include prompt+rationale (got {keys})"
        )

        br = sse_events[2]["value"]["beginRendering"]
        (_ok if br["root"] == "root" and br["surfaceId"] == "eda-action" else _bad)(
            "beginRendering root='root' + surfaceId matches"
        )


async def t4_action_strings() -> None:
    print("\n=== T4. each A2UI action string round-trips to USER_ACTION on /agui ===")
    for action in ("confirm_transform", "dismiss", "stop_training", "keep_training"):
        sse_task = asyncio.create_task(_sse_wait_for_user_action(action, timeout=8.0))
        await asyncio.sleep(0.4)

        async with websockets.connect(WS_URL, open_timeout=5) as ws:
            await ws.send(json.dumps({
                "type": "interaction", "session_id": SID, "action": action,
                "context": {"column": "price", "transform": "log"},
            }))
            # these actions are not agent-routed; they ack instantly
            try:
                _ = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except asyncio.TimeoutError:
                pass

        events = await asyncio.wait_for(sse_task, timeout=10.0)
        matched = bool(events) and (
            events[-1].get("event") == "USER_ACTION"
            or (events[-1].get("event") == "CUSTOM"
                and events[-1].get("name") == "USER_ACTION")
        )
        if matched:
            _ok(f"{action}: round-tripped to USER_ACTION")
        else:
            _bad(f"{action}: no USER_ACTION event (got {events})")


# ---------------------------------------------------------------------------
# T5. projection tools
# ---------------------------------------------------------------------------
def t5_tools() -> None:
    print("\n=== T5. project_3d / kde_surface / corr_field ===")
    df = pd.read_csv(ROOT / "data" / "sample.csv")

    t0 = time.time()
    s3 = project_3d(df, color_by="category", max_points=500)
    dt = (time.time() - t0) * 1000
    try:
        Scatter3D.model_validate(s3)
        _ok(f"project_3d validates Scatter3D ({len(s3['points'])} pts, {dt:.0f} ms, "
            f"axes={s3['axes']}, title={s3['title'][:50]}…)")
    except Exception as e:
        _bad(f"project_3d schema: {e}")

    t0 = time.time()
    surf = kde_surface(df, "pca1", "pca2", grid=32)
    dt = (time.time() - t0) * 1000
    try:
        Surface.model_validate(surf)
        zmin = min(min(r) for r in surf["z"])
        zmax = max(max(r) for r in surf["z"])
        _ok(f"kde_surface validates Surface (grid={surf['grid']}, z∈[{zmin:.3f},{zmax:.3f}], {dt:.0f} ms)")
    except Exception as e:
        _bad(f"kde_surface schema: {e}")

    t0 = time.time()
    fld = corr_field(df)
    dt = (time.time() - t0) * 1000
    try:
        CorrField.model_validate(fld)
        _ok(f"corr_field validates CorrField ({len(fld['labels'])}×{len(fld['labels'])}, {dt:.0f} ms)")
    except Exception as e:
        _bad(f"corr_field schema: {e}")


# ---------------------------------------------------------------------------
async def main() -> int:
    t1_healthz()
    await t2_t3_select_point_triggers_surface()
    await t4_action_strings()
    t5_tools()

    print("\n=== summary ===")
    print(f"surfaces frozen: {sorted(SURFACE_IDS)}")
    print(f"actions  frozen: {sorted(ACTIONS)}")
    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
