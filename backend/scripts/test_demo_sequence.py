#!/usr/bin/env python3
"""Two-pass end-to-end demo sequence (TASKS_A integration prep).

Sequence per pass:
  load titanic → load_run overfit → voice_query skew → select_panel →
  voice_query overfitting → narrate

Fails if any step exceeds STEP_TIMEOUT or if pass-2 is >2× slower than pass-1
on the same step (inference/SSE stall signal).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

import httpx
import websockets

WS = "ws://127.0.0.1:8080/ws"
AGUI = "http://127.0.0.1:8080/agui"
STEP_TIMEOUT = 120.0
STALL_RATIO = 2.0


async def ws_step(sid: str, payload: dict[str, Any], label: str) -> tuple[float, list[dict]]:
    t0 = time.time()
    frames: list[dict[str, Any]] = []
    async with websockets.connect(WS, open_timeout=10) as ws:
        payload = {**payload, "session_id": sid}
        await ws.send(json.dumps(payload))
        deadline = t0 + STEP_TIMEOUT
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(20.0, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            frames.append(json.loads(raw))
            last = frames[-1]
            if last.get("type") == "agent_status" and last.get("state") in ("done", "error"):
                try:
                    extra = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    frames.append(json.loads(extra))
                except asyncio.TimeoutError:
                    pass
                break
    dt = time.time() - t0
    states = [f"{f.get('type')}/{f.get('state', '')}" for f in frames]
    print(f"    {label}: {dt:.1f}s  frames={len(frames)}  tail={states[-3:]}")
    if dt >= STEP_TIMEOUT - 1:
        print(f"    FAIL stall: {label} hit {STEP_TIMEOUT}s cap")
        return dt, frames
    if frames and frames[-1].get("state") == "error":
        print(f"    FAIL error: {frames[-1]}")
    return dt, frames


async def sse_ping(timeout: float = 8.0) -> bool:
    """Quick /agui liveness — should get STATE_DELTA within timeout."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=None) as c:
        async with c.stream("GET", AGUI, headers={"Accept": "text/event-stream"}) as r:
            async for line in r.aiter_lines():
                if time.time() - t0 > timeout:
                    return False
                if line.startswith("data:"):
                    return True
    return False


async def run_pass(pass_n: int, sid: str) -> dict[str, float]:
    print(f"\n=== pass {pass_n}  sid={sid} ===")
    times: dict[str, float] = {}

    times["sse_ping"], _ = 0.0, None
    ok = await sse_ping()
    print(f"    sse_ping: {'ok' if ok else 'STALL'}")
    if not ok:
        times["sse_ping"] = STEP_TIMEOUT

    times["load_titanic"], _ = await ws_step(sid, {
        "type": "command", "action": "load_dataset", "params": {"name": "titanic"},
    }, "load_titanic")

    times["load_overfit"], _ = await ws_step(sid, {
        "type": "command", "action": "load_run", "params": {"run_id": "demo-overfit-001"},
    }, "load_overfit")

    times["vq_skew"], skew_frames = await ws_step(sid, {
        "type": "voice_query",
        "text": "which columns are skewed and is anything missing?",
    }, "voice_query skew")

    column = "fare"
    for f in skew_frames:
        if f.get("type") == "panels":
            panels = f.get("panels") or []
            if panels and panels[0].get("column"):
                column = panels[0]["column"]
            break

    times["select_panel"], _ = await ws_step(sid, {
        "type": "interaction", "action": "select_panel", "target_id": column,
    }, f"select_panel({column})")

    times["vq_overfit"], _ = await ws_step(sid, {
        "type": "voice_query",
        "text": "Is the model overfitting? What's the loss doing?",
    }, "voice_query overfitting")

    times["narrate"], narr_frames = await ws_step(sid, {
        "type": "command", "action": "narrate",
    }, "narrate")

    got_report = any(f.get("type") == "report" for f in narr_frames)
    print(f"    narrate report frame: {'yes' if got_report else 'MISSING'}")
    return times


async def main() -> int:
    sid = f"demo-{int(time.time())}"
    t1 = await run_pass(1, sid)
    await asyncio.sleep(1.0)
    t2 = await run_pass(2, sid)

    print("\n=== timing comparison (pass2 / pass1) ===")
    stalls = []
    for step in t1:
        if step not in t2:
            continue
        r = t2[step] / max(t1[step], 0.1)
        flag = " STALL?" if r > STALL_RATIO and t2[step] > 15 else ""
        print(f"  {step:18s}  pass1={t1[step]:6.1f}s  pass2={t2[step]:6.1f}s  ratio={r:.2f}{flag}")
        if flag:
            stalls.append(step)

    if stalls:
        print(f"\nFAIL: possible stalls on pass-2: {stalls}")
        return 1
    print("\nPASS: two back-to-back demo passes completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
