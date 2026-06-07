#!/usr/bin/env python3
"""Phase test: narrator + integration confirmations.

Asserts:
  N1.  /healthz still healthy + contracts_version=0.0.2.
  N2.  After running an EDA voice_query (so findings exist) and a training
       voice_query (so a run verdict exists), a `narrate` command returns a
       Report frame with sections referencing real columns / loss numbers.
  N3.  Direct narrator (no session state) gracefully emits verdict
       "insufficient_data".
  N4.  Router handles "summarise what we found" by handing off to narrator,
       producing the same Report.
  N5.  Integration-item 1: eda-action surface emitted earlier in the session
       carries `prompt` AND `rationale` keys in dataModelUpdate contents.
  N6.  Integration-item 2: eda-findings rows expose `column`, `flag`, `note`
       (singular `flag`). Verified directly from a2ui.surfaces.
  N7.  Integration-item 3: BROADCAST_NOTE_TRAINING.md contains the FROZEN
       marker (so Person C has a written commitment).
  N8.  Integration-item 4: Button v0.8 mode emits flat `action`; v0.9 mode
       emits `action.event.name`. Also verifies POST /action accepts the
       v0.9 callback shape and emits USER_ACTION on /agui.
  N9.  Prior regressions still green (phase1, a2ui, eda, router).

Usage:
    uvicorn backend.main:app --port 8080 &
    python backend/scripts/test_phase_narrator.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WS_URL = "ws://127.0.0.1:8080/ws"
HEALTH_URL = "http://127.0.0.1:8080/healthz"
AGUI_URL = "http://127.0.0.1:8080/agui"
ACTION_URL      = "http://127.0.0.1:8080/action"
AGUI_ACTION_URL = "http://127.0.0.1:8080/agui/action"
SID = f"narr-{int(time.time())}"

FAILURES: list[str] = []


def _ok(msg: str) -> None: print(f"  PASS  {msg}")
def _bad(msg: str) -> None: print(f"  FAIL  {msg}"); FAILURES.append(msg)


# ---------------------------------------------------------------------------
async def ws_round_trip(payload: dict[str, Any], hard_cap: float = 90.0,
                        silence: float = 18.0) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    deadline = time.time() + hard_cap
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        await ws.send(json.dumps(payload))
        while time.time() < deadline:
            try:
                f = await asyncio.wait_for(ws.recv(), timeout=silence)
                frames.append(json.loads(f))
                if (frames[-1].get("type") == "agent_status"
                        and frames[-1].get("state") in ("done", "error")):
                    try:
                        f2 = await asyncio.wait_for(ws.recv(), timeout=0.4)
                        frames.append(json.loads(f2))
                    except asyncio.TimeoutError:
                        pass
                    break
            except asyncio.TimeoutError:
                break
    return frames


async def sse_until(predicate: Callable[[list[dict[str, Any]]], bool],
                    timeout: float = 60.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", AGUI_URL,
                                 headers={"Accept": "text/event-stream"}) as resp:
            async for line in resp.aiter_lines():
                if time.time() > deadline:
                    break
                if not line.startswith("data:"):
                    continue
                payload = json.loads(line[len("data:"):].strip())
                if payload.get("event") == "STATE_DELTA":
                    continue
                out.append(payload)
                if predicate(out):
                    break
    return out


# ---------------------------------------------------------------------------
def n1_health() -> None:
    print("\n=== N1. /healthz ===")
    r = httpx.get(HEALTH_URL, timeout=5.0).json()
    (_ok if r.get("contracts_version") == "0.0.2" else _bad)(
        f"contracts_version=0.0.2 (got {r.get('contracts_version')})")
    (_ok if r.get("redis") else _bad)("redis ping ok")


# ---------------------------------------------------------------------------
async def _prime_session() -> None:
    """Run a small EDA + training query so the narrator has something to read."""
    print("  priming: load_dataset titanic")
    await ws_round_trip({"type": "command", "session_id": SID,
                         "action": "load_dataset", "params": {"name": "titanic"}},
                        hard_cap=10, silence=4)

    print("  priming: load_run demo-overfit-001")
    await ws_round_trip({"type": "command", "session_id": SID,
                         "action": "load_run",
                         "params": {"run_id": "demo-overfit-001"}},
                        hard_cap=10, silence=4)

    print("  priming: EDA voice_query (writes findings)")
    await ws_round_trip({"type": "voice_query", "session_id": SID,
                         "text": "Which columns are missing or skewed?"},
                        hard_cap=120, silence=20)

    print("  priming: training voice_query (writes verdict)")
    await ws_round_trip({"type": "voice_query", "session_id": SID,
                         "text": "Is the model overfitting?"},
                        hard_cap=120, silence=20)


async def n2_narrate_grounded() -> None:
    print("\n=== N2. narrate command produces grounded Report ===")
    await _prime_session()

    frames = await ws_round_trip(
        {"type": "command", "session_id": SID, "action": "narrate"},
        hard_cap=120, silence=20,
    )
    for f in frames:
        t = f.get("type")
        if t == "report":
            print(f"    ← report verdict={f.get('verdict')!r} "
                  f"sections={[s.get('title') for s in f.get('sections', [])]}")
        elif t == "speech":
            print(f"    ← speech: {f.get('text')!r}")
        elif t == "agent_status":
            print(f"    ← {f.get('agent')}/{f.get('state')}: {f.get('message')!r}")

    report = next((f for f in frames if f.get("type") == "report"), None)
    (_ok if report else _bad)("got a `report` frame in response to narrate")
    if not report:
        return

    speak = report.get("speak")
    (_ok if speak else _bad)(f"report.speak=True (got {speak!r})")

    verdict = (report.get("verdict") or "").lower()
    (_ok if verdict and verdict != "insufficient_data" else _bad)(
        f"verdict set to a real value (got {verdict!r})")

    sections = report.get("sections") or []
    (_ok if len(sections) >= 2 else _bad)(
        f">=2 sections (got {len(sections)})")

    # Sections should mention at least one real artefact: a titanic column OR
    # an overfit-run number / keyword. We don't require both because the LLM
    # may compress.
    bodies = " ".join((s.get("body") or "") for s in sections).lower()
    grounded_hits = [
        kw for kw in (
            "deck", "fare", "age", "sibsp", "cabin",          # titanic cols
            "overfit", "val_loss", "epoch", "step",
            "0.5", "0.9", "demo-overfit",                       # numbers / id
        )
        if kw in bodies
    ]
    (_ok if grounded_hits else _bad)(
        f"sections cite real artefacts (got hits: {grounded_hits} | bodies={bodies[:240]!r})")


async def n3_insufficient_data() -> None:
    print("\n=== N3. narrator with empty session → insufficient_data ===")
    empty_sid = f"narr-empty-{int(time.time())}"
    frames = await ws_round_trip(
        {"type": "command", "session_id": empty_sid, "action": "narrate"},
        hard_cap=90, silence=15,
    )
    report = next((f for f in frames if f.get("type") == "report"), None)
    (_ok if report else _bad)("got a `report` frame even with empty session")
    if report:
        verdict = (report.get("verdict") or "").lower()
        (_ok if verdict in ("insufficient_data", "mixed") else _bad)(
            f"empty session yields insufficient_data or mixed (got {verdict!r})")


async def n4_router_narrator_handoff() -> None:
    print("\n=== N4. router handoff to narrator on summary phrasing ===")
    # Prime again to make sure there's something to summarise on this sid.
    await _prime_session()

    def _to(e: dict[str, Any]) -> str | None:
        p = e.get("args") or e.get("value") or {}
        return p.get("to") if isinstance(p, dict) else None

    sse_task = asyncio.create_task(sse_until(
        lambda evs: any(e.get("event") == "HANDOFF" and _to(e) == "narrator" for e in evs),
        timeout=120.0,
    ))
    await asyncio.sleep(0.3)

    frames = await ws_round_trip(
        {"type": "voice_query", "session_id": SID,
         "text": "Give me a wrap-up of what we found this session."},
        hard_cap=120, silence=20,
    )
    for f in frames:
        if f.get("type") in ("report", "speech", "agent_status"):
            print(f"    ← {f.get('type')}: "
                  f"{f.get('text') or f.get('verdict') or f.get('message')!r}")

    sse_events = await asyncio.wait_for(sse_task, timeout=120.0)
    handoffs_to_narrator = [e for e in sse_events
                            if e.get("event") == "HANDOFF" and _to(e) == "narrator"]
    (_ok if handoffs_to_narrator else _bad)(
        f"HANDOFF to narrator on /agui (got: {[_to(e) for e in sse_events if e.get('event') == 'HANDOFF']})")

    report = next((f for f in frames if f.get("type") == "report"), None)
    (_ok if report else _bad)("router-driven narrate produced a Report frame")


# ---------------------------------------------------------------------------
def n5_n6_surface_contracts() -> None:
    """Code-level confirmation of items 1 and 2 — does not need the server."""
    print("\n=== N5/N6. eda-action + eda-findings surface contracts ===")
    from backend.a2ui import surfaces

    comps, data, sfc = surfaces.eda_action(column="fare", transform="log")
    (_ok if sfc == "eda-action" else _bad)(f"surface_id == eda-action (got {sfc!r})")
    (_ok if "prompt" in data and "rationale" in data else _bad)(
        f"eda-action data has prompt + rationale (got keys: {list(data)})")
    paths = [c["component"]["Text"]["text"].get("path")
             for c in comps if "Text" in c.get("component", {})]
    (_ok if "/prompt" in paths and "/rationale" in paths else _bad)(
        f"eda-action components bind /prompt + /rationale (got: {paths})")

    rows = [{"column": "fare", "flag": "right_skewed", "note": "log candidate"}]
    comps, data, sfc = surfaces.eda_findings(rows)
    (_ok if sfc == "eda-findings" else _bad)(f"surface_id == eda-findings (got {sfc!r})")
    row0 = (data.get("findings") or [{}])[0]
    keys = set(row0.keys())
    (_ok if keys == {"column", "flag", "note"} else _bad)(
        f"eda-findings row keys == {{column, flag, note}} (got {keys})")
    bindings = [c["component"]["Text"]["text"].get("path")
                for c in comps if "Text" in c.get("component", {})]
    (_ok if "/column" in bindings and "/flag" in bindings else _bad)(
        f"eda-findings components bind /column + /flag (got: {bindings})")


def n7_broadcast_frozen() -> None:
    print("\n=== N7. replay_run_history schema FROZEN in BROADCAST_NOTE_TRAINING.md ===")
    p = ROOT / "backend" / "BROADCAST_NOTE_TRAINING.md"
    txt = p.read_text() if p.exists() else ""
    (_ok if "FROZEN" in txt and "narrator-phase" in txt else _bad)(
        f"FROZEN marker present (path={p}, size={len(txt)})")


# ---------------------------------------------------------------------------
async def n8_button_modes_and_action_endpoint() -> None:
    print("\n=== N8. A2UI button modes + POST /action ===")

    # --- v08 (default in this test run) ---
    # The surfaces module reads BUTTON_MODE once at import time, so just check
    # the function honours the explicit `mode=` override.
    from backend.a2ui import surfaces

    comps_v8 = surfaces._button("b", label="Apply", action="confirm_transform",
                                context={"column": "fare", "transform": "log"},
                                mode="v08")
    btn = comps_v8["component"]["Button"]
    (_ok if isinstance(btn.get("action"), str)
          and btn["action"] == "confirm_transform" else _bad)(
        f"v08 button: action is a flat string (got {btn.get('action')!r})")
    ctx_contents = (btn.get("context") or {}).get("contents") or []
    (_ok if any(c.get("key") == "column" for c in ctx_contents) else _bad)(
        f"v08 button: context.contents has key=column (got {ctx_contents})")

    # --- v09 (canonical action.event shape) ---
    comps_v9 = surfaces._button("b", label="Apply", action="confirm_transform",
                                context={"column": "fare", "transform": "log"},
                                mode="v09")
    btn = comps_v9["component"]["Button"]
    act = btn.get("action")
    (_ok if isinstance(act, dict) and isinstance(act.get("event"), dict)
          and act["event"].get("name") == "confirm_transform" else _bad)(
        f"v09 button: action.event.name == 'confirm_transform' (got {act!r})")
    ctx = (act.get("event") or {}).get("context") if isinstance(act, dict) else None
    (_ok if isinstance(ctx, dict) and ctx.get("column") == "fare" else _bad)(
        f"v09 button: literal context {{column:'fare'}} (got {ctx!r})")
    (_ok if "context" not in btn else _bad)(
        f"v09 button: no sibling `context` outside action (got keys: {list(btn)})")

    # --- POST /action accepts v0.9 callback shape and emits USER_ACTION on /agui ---
    def _is_user_action_for(name: str, evs: list[dict[str, Any]]) -> bool:
        for e in evs:
            if e.get("event") != "USER_ACTION":
                continue
            a = e.get("args") or {}
            if isinstance(a, dict) and a.get("action") == name:
                return True
        return False

    sse_task = asyncio.create_task(sse_until(
        lambda evs: _is_user_action_for("confirm_transform", evs),
        timeout=15.0,
    ))
    await asyncio.sleep(0.2)

    payload = {
        "session_id": SID,
        "action": {
            "name": "confirm_transform",
            "surfaceId": "eda-action",
            "sourceComponentId": "confirm",
            "timestamp": "2026-06-06T17:00:00Z",
            "context": {"column": "fare", "transform": "log"},
        },
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(ACTION_URL, json=payload)
    (_ok if resp.status_code == 200 else _bad)(
        f"POST /action returned 200 (got {resp.status_code}, body={resp.text[:200]!r})")
    body = resp.json() if resp.status_code == 200 else {}
    iact = body.get("interaction") or {}
    (_ok if iact.get("action") == "confirm_transform"
          and iact.get("context", {}).get("column") == "fare" else _bad)(
        f"POST /action normalises v0.9 shape to Interaction (got {iact})")

    sse_events = await asyncio.wait_for(sse_task, timeout=15.0)
    (_ok if _is_user_action_for("confirm_transform", sse_events) else _bad)(
        f"USER_ACTION emitted on /agui after POST /action "
        f"(events seen: {[e.get('event') for e in sse_events]})")

    # --- POST /agui/action accepts Person C's canonical {userAction:{...}} shape ---
    sse_task2 = asyncio.create_task(sse_until(
        lambda evs: _is_user_action_for("dismiss", evs),
        timeout=15.0,
    ))
    await asyncio.sleep(0.2)
    person_c_payload = {
        "session_id": SID,
        "userAction": {
            "name": "dismiss",
            "surfaceId": "eda-action",
            "context": {"column": "fare"},
        },
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(AGUI_ACTION_URL, json=person_c_payload)
    (_ok if resp.status_code == 200 else _bad)(
        f"POST /agui/action returned 200 (got {resp.status_code}, body={resp.text[:200]!r})")
    body = resp.json() if resp.status_code == 200 else {}
    iact = body.get("interaction") or {}
    (_ok if iact.get("action") == "dismiss"
          and iact.get("target_id") == "eda-action"
          and iact.get("context", {}).get("column") == "fare" else _bad)(
        f"POST /agui/action normalises {{userAction:{{...}}}} to Interaction (got {iact})")

    sse_events2 = await asyncio.wait_for(sse_task2, timeout=15.0)
    ua_via = next(((e.get("args") or {}).get("via") for e in sse_events2
                   if e.get("event") == "USER_ACTION"
                   and (e.get("args") or {}).get("action") == "dismiss"), None)
    (_ok if ua_via == "POST /agui/action" else _bad)(
        f"USER_ACTION via='POST /agui/action' on /agui (got via={ua_via!r})")


# ---------------------------------------------------------------------------
def n9_regressions() -> None:
    print("\n=== N9. prior phase regressions ===")
    env = dict(os.environ); env["MPLCONFIGDIR"] = "/tmp/mpl"
    # Small cooldown between subprocesses so the SSE queue from the previous
    # test fully drains and the LLM inference path isn't contended.
    for i, (name, script) in enumerate((
        ("phase1", "backend/scripts/test_phase1.py"),
        ("a2ui",   "backend/scripts/test_phase_a2ui.py"),
        ("eda",    "backend/scripts/test_phase_eda.py"),
        ("router", "backend/scripts/test_phase_router.py"),
    )):
        if i > 0:
            time.sleep(2.0)
        r = subprocess.run([sys.executable, script],
                           capture_output=True, text=True, env=env,
                           timeout=600)
        last = (r.stdout.strip().split("\n")[-1] if r.stdout else "")
        (_ok if r.returncode == 0 else _bad)(
            f"{name} regression: exit={r.returncode}, last={last!r}"
        )


# ---------------------------------------------------------------------------
async def main() -> int:
    n1_health()
    await n2_narrate_grounded()
    await n3_insufficient_data()
    await n4_router_narrator_handoff()
    n5_n6_surface_contracts()
    n7_broadcast_frozen()
    await n8_button_modes_and_action_endpoint()
    n9_regressions()

    print("\n=== summary ===")
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
