#!/usr/bin/env python3
"""Test: problem_type agent via router voice_query.

Asserts:
  P1. load_dataset titanic + "what problem are we solving?" routes to
      problem_type (HANDOFF on /agui), returns Speech with non-empty
      problem_type + model_suggestion in the done agent_status.
  P2. Existing regressions: router, eda, a2ui, phase1 still green.

Usage:
    uvicorn backend.main:app --host 0.0.0.0 --port 8080 &
    python backend/scripts/test_problem_type.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
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
SID = f"pt-{int(time.time())}"

FAILURES: list[str] = []


def _ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def _bad(msg: str) -> None:
    print(f"  FAIL  {msg}")
    FAILURES.append(msg)


async def ws_round_trip(payload: dict[str, Any], hard_cap: float = 45.0,
                        silence: float = 12.0) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    deadline = time.time() + hard_cap
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        await ws.send(json.dumps(payload))
        while time.time() < deadline:
            try:
                f = await asyncio.wait_for(ws.recv(), timeout=silence)
                frames.append(json.loads(f))
                if (frames[-1].get("type") == "agent_status"
                        and frames[-1].get("state") in ("done", "error")
                        and frames[-1].get("agent") == "problem_type"):
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


def _handoff_to(e: dict[str, Any]) -> str | None:
    payload = e.get("args") or e.get("value") or {}
    return payload.get("to") if isinstance(payload, dict) else None


async def p1_problem_type_route() -> None:
    print("\n=== P1. problem_type voice_query (titanic) ===")

    await ws_round_trip(
        {"type": "command", "session_id": SID,
         "action": "load_dataset", "params": {"name": "titanic"}},
        hard_cap=10.0, silence=2.0,
    )

    def _got_handoff(evs: list[dict[str, Any]]) -> bool:
        return any(
            e.get("event") == "HANDOFF" and _handoff_to(e) == "problem_type"
            for e in evs
        )

    sse_task = asyncio.create_task(sse_until(_got_handoff, timeout=60.0))
    await asyncio.sleep(0.4)

    frames = await ws_round_trip(
        {"type": "voice_query", "session_id": SID,
         "text": "what problem are we solving?"},
        hard_cap=60.0, silence=15.0,
    )
    sse_events = await asyncio.wait_for(sse_task, timeout=60.0)

    for f in frames:
        if f.get("type") == "speech":
            print(f"    ← speech: {f.get('text')!r}")
        elif f.get("type") == "agent_status":
            print(f"    ← {f.get('agent')}/{f.get('state')}: {f.get('message')!r}")

    speech = next((f for f in frames if f.get("type") == "speech"), None)
    done = next(
        (f for f in frames
         if f.get("type") == "agent_status"
         and f.get("agent") == "problem_type"
         and f.get("state") == "done"),
        None,
    )
    handoffs = [e for e in sse_events if e.get("event") == "HANDOFF"]
    pt_handoff = [e for e in handoffs if _handoff_to(e) == "problem_type"]

    (_ok if speech and speech.get("text") else _bad)(
        f"Speech frame returned (got {speech})"
    )
    (_ok if pt_handoff else _bad)(
        f"HANDOFF to problem_type on /agui (targets={[_handoff_to(h) for h in handoffs]})"
    )
    (_ok if done else _bad)(
        f"problem_type agent_status done (got agents={[f.get('agent') for f in frames]})"
    )

    msg = (done or {}).get("message", "")
    (_ok if re.search(r"problem_type=(classification|regression)", msg) else _bad)(
        f"status cites problem_type classification|regression (got {msg!r})"
    )
    (_ok if "model=" in msg and not msg.endswith("model=") else _bad)(
        f"status cites non-empty model_suggestion (got {msg!r})"
    )

    speech_text = (speech or {}).get("text", "").lower()
    (_ok if any(w in speech_text for w in ("classification", "regression")) else _bad)(
        f"speech mentions classification or regression (got {speech_text!r})"
    )


def p2_regressions() -> None:
    print("\n=== P2. existing regressions ===")
    env = dict(os.environ)
    env["MPLCONFIGDIR"] = "/tmp/mpl"
    for name, script in (
        ("phase1", "backend/scripts/test_phase1.py"),
        ("a2ui", "backend/scripts/test_phase_a2ui.py"),
        ("eda", "backend/scripts/test_phase_eda.py"),
        ("router", "backend/scripts/test_phase_router.py"),
    ):
        r = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, env=env, timeout=360,
        )
        last = (r.stdout.strip().split("\n")[-1] if r.stdout else "")
        (_ok if r.returncode == 0 else _bad)(
            f"{name} regression: exit={r.returncode}, last={last!r}"
        )
        if r.returncode != 0 and r.stderr:
            print(r.stderr[-800:])


async def main() -> int:
    r = httpx.get(HEALTH_URL, timeout=5.0)
    if r.status_code != 200:
        _bad(f"/healthz not reachable ({r.status_code})")
        return 1
    _ok("/healthz reachable")

    await p1_problem_type_route()
    p2_regressions()

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
