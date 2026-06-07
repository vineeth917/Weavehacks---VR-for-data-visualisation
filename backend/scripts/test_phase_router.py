#!/usr/bin/env python3
"""Phase test: router + training-monitor.

Asserts:
  T1.  /healthz still healthy
  T2.  EDA-flavoured voice_query routes to EDA with a visible HANDOFF event
       to "eda" on /agui, returns Speech + Panels + eda-findings surface.
  T3.  Training-flavoured voice_query (after `load_run` demo-overfit-001)
       routes to training_monitor with HANDOFF to "training_monitor", returns
       Speech that quotes real numbers (val_loss values, "overfitting"),
       a `training_update` frame, and a training-verdict surface on /agui.
  T4.  Healthy run: verdict flips to "healthy", suggested_action "keep_training".
  T5.  Leakage run: verdict flips to "leakage".
  T6.  Phase-1, Phase-A2UI, Phase-EDA regressions still green.

Usage:
    uvicorn backend.main:app --port 8080 &
    python backend/scripts/test_phase_router.py
"""
from __future__ import annotations

import asyncio
import json
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
SID = f"router-{int(time.time())}"

FAILURES: list[str] = []
WS_TIMEOUT = 60.0


def _ok(msg: str) -> None: print(f"  PASS  {msg}")
def _bad(msg: str) -> None: print(f"  FAIL  {msg}"); FAILURES.append(msg)


# ---------------------------------------------------------------------------
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
                    timeout: float = WS_TIMEOUT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", AGUI_URL, headers={"Accept": "text/event-stream"}) as resp:
            async for line in resp.aiter_lines():
                if time.time() > deadline: break
                if not line.startswith("data:"): continue
                payload = json.loads(line[len("data:"):].strip())
                if payload.get("event") == "STATE_DELTA": continue
                out.append(payload)
                if predicate(out): break
    return out


# ---------------------------------------------------------------------------
def t1_health() -> None:
    print("\n=== T1. /healthz ===")
    r = httpx.get(HEALTH_URL, timeout=5.0).json()
    (_ok if r.get("contracts_version") == "0.0.2" else _bad)(
        f"contracts_version=0.0.2 (got {r.get('contracts_version')})")
    (_ok if r.get("redis") else _bad)("redis ping ok")


# ---------------------------------------------------------------------------
async def _check_handoff_and_specialist(
    query: str,
    *,
    expect_target: str,                 # "eda" or "training_monitor"
    expect_surface: str,                # "eda-findings" or "training-verdict"
    pre_send: list[dict[str, Any]] | None = None,
    extra_speech_asserts: list[tuple[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Returns the captured frames + sse events for further assertions."""
    if pre_send:
        for p in pre_send:
            await ws_round_trip(p, silence=2.0)

    def _to_field(e: dict[str, Any]) -> str | None:
        # First-class HANDOFF events carry the payload in `args`; CUSTOM events in `value`.
        payload = e.get("args") or e.get("value") or {}
        if not isinstance(payload, dict):
            return None
        return payload.get("to")

    def _surface_field(e: dict[str, Any]) -> str | None:
        v = e.get("value")
        if not isinstance(v, dict):
            return None
        br = v.get("beginRendering")
        if not isinstance(br, dict):
            return None
        return br.get("surfaceId")

    sse_task = asyncio.create_task(sse_until(
        lambda evs: (
            any(e.get("event") == "HANDOFF" and _to_field(e) == expect_target for e in evs)
            and any(e.get("event") == "CUSTOM" and _surface_field(e) == expect_surface for e in evs)
        ),
        timeout=90.0,
    ))
    await asyncio.sleep(0.5)

    t0 = time.time()
    frames = await ws_round_trip(
        {"type": "voice_query", "session_id": SID, "text": query},
        hard_cap=90.0, silence=15.0,
    )
    dt = time.time() - t0
    print(f"  ws latency: {dt:.1f}s, frames={len(frames)}")
    for f in frames:
        t = f.get("type")
        if t == "speech":
            print(f"    ← speech: {f.get('text')!r}")
        elif t == "panels":
            print(f"    ← panels: {len(f.get('panels', []))}")
        elif t == "training_update":
            print(f"    ← training_update run={f.get('run_id')} step={f.get('step')} metrics={f.get('metrics')}")
        elif t == "agent_status":
            print(f"    ← {f.get('agent')}/{f.get('state')}: {f.get('message')!r}")

    sse_events = await asyncio.wait_for(sse_task, timeout=90.0)

    # ---- assertions ----
    types = [f.get("type") for f in frames]
    (_ok if "speech" in types else _bad)(f"got speech ({types})")

    handoffs = [e for e in sse_events if e.get("event") == "HANDOFF"]
    correct_handoff = [e for e in handoffs if _to_field(e) == expect_target]
    (_ok if correct_handoff else _bad)(
        f"HANDOFF emitted to {expect_target} on /agui ({len(handoffs)} handoffs total, "
        f"targets={[_to_field(h) for h in handoffs]})"
    )

    surface_envs = [e for e in sse_events if e.get("event") == "CUSTOM" and
                    _surface_field(e) == expect_surface]
    (_ok if surface_envs else _bad)(
        f"{expect_surface} surface beginRendering on /agui"
    )

    speech_text = next((f.get("text", "") for f in frames if f.get("type") == "speech"), "")
    if extra_speech_asserts:
        for label, needles in extra_speech_asserts:
            hits = [n for n in needles if re.search(n, speech_text, re.I)]
            (_ok if hits else _bad)(f"{label}: speech matches one of {needles} (got {speech_text!r})")

    return {"frames": frames, "sse": sse_events, "latency": dt}


async def t2_eda_route() -> None:
    print("\n=== T2. EDA-flavoured voice_query routes to EDA ===")
    await _check_handoff_and_specialist(
        "Which columns are missing or skewed in this dataset?",
        expect_target="eda",
        expect_surface="eda-findings",
        pre_send=[{"type": "command", "session_id": SID,
                   "action": "load_dataset", "params": {"name": "titanic"}}],
        extra_speech_asserts=[
            ("EDA speech cites titanic columns",
             [r"deck", r"fare", r"age", r"sibsp", r"missing"]),
        ],
    )


async def t3_training_overfit_route() -> None:
    print("\n=== T3. training voice_query → training_monitor (overfit run) ===")
    res = await _check_handoff_and_specialist(
        "Is the model overfitting? What's the loss doing?",
        expect_target="training_monitor",
        expect_surface="training-verdict",
        pre_send=[{"type": "command", "session_id": SID,
                   "action": "load_run", "params": {"run_id": "demo-overfit-001"}}],
        extra_speech_asserts=[
            ("speech cites overfitting or real loss numbers",
             [r"overfit", r"0\.[0-9]{2,3}", r"val[_ ]?loss", r"epoch"]),
        ],
    )
    # also verify the training_update frame fired
    tu = next((f for f in res["frames"] if f.get("type") == "training_update"), None)
    (_ok if tu and tu.get("run_id") == "demo-overfit-001" else _bad)(
        f"training_update frame for demo-overfit-001 (got {tu})"
    )


async def t4_training_healthy_route() -> None:
    print("\n=== T4. healthy run verdict ===")
    res = await _check_handoff_and_specialist(
        "How's training going on this run?",
        expect_target="training_monitor",
        expect_surface="training-verdict",
        pre_send=[{"type": "command", "session_id": SID,
                   "action": "load_run", "params": {"run_id": "demo-healthy-002"}}],
        extra_speech_asserts=[
            ("speech cites healthy or numerical val_loss",
             [r"healthy", r"keep", r"0\.[0-9]{2,3}"]),
        ],
    )
    last = next((f for f in reversed(res["frames"]) if f.get("type") == "agent_status"), {})
    msg = (last.get("message") or "").lower()
    (_ok if "healthy" in msg else _bad)(f"verdict reported as 'healthy' in status (got {msg!r})")


async def t5_training_leakage_route() -> None:
    print("\n=== T5. leakage run verdict ===")
    res = await _check_handoff_and_specialist(
        "What does the validation loss look like on this run?",
        expect_target="training_monitor",
        expect_surface="training-verdict",
        pre_send=[{"type": "command", "session_id": SID,
                   "action": "load_run", "params": {"run_id": "demo-leakage-003"}}],
        extra_speech_asserts=[
            ("speech cites leakage or below-train",
             [r"leak", r"below", r"98%", r"suspicious"]),
        ],
    )
    last = next((f for f in reversed(res["frames"]) if f.get("type") == "agent_status"), {})
    msg = (last.get("message") or "").lower()
    (_ok if "leakage" in msg else _bad)(f"verdict reported as 'leakage' in status (got {msg!r})")


def t6_regressions() -> None:
    print("\n=== T6. prior phase regressions ===")
    import os
    env = dict(os.environ); env["MPLCONFIGDIR"] = "/tmp/mpl"
    for name, script in (
        ("phase1", "backend/scripts/test_phase1.py"),
        ("a2ui",   "backend/scripts/test_phase_a2ui.py"),
        ("eda",    "backend/scripts/test_phase_eda.py"),
    ):
        r = subprocess.run([sys.executable, script],
                           capture_output=True, text=True, env=env, timeout=240)
        last = (r.stdout.strip().split("\n")[-1] if r.stdout else "")
        (_ok if r.returncode == 0 else _bad)(
            f"{name} regression: exit={r.returncode}, last={last!r}"
        )


# ---------------------------------------------------------------------------
async def main() -> int:
    t1_health()
    await t2_eda_route()
    await t3_training_overfit_route()
    await t4_training_healthy_route()
    await t5_training_leakage_route()
    t6_regressions()

    print("\n=== summary ===")
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES: print("  -", f)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
