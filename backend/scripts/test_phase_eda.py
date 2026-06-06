#!/usr/bin/env python3
"""Phase test: real EDA agent on /ws + A2UI surfaces on /agui.

What it asserts:
  T1.  /healthz still reports contracts_version=0.0.2
  T2.  voice_query on titanic (default dataset) → speech cites real columns
       (deck/fare/age/sibsp), panels arrive (PNGs ≤80 KB), and an
       eda-findings A2UI surface fires on /agui with non-empty data.
  T3.  command load_dataset name=sample then voice_query → speech changes to
       cite our synthetic columns (price/balance/tx_amount).
  T4.  interaction select_point on r10/r25/r80 → agent returns real speech
       referencing row data, highlight, and an eda-action A2UI surface fires.
  T5.  Redis has the dataset profile + findings for the session.
  T6.  Phase-1 (profiling/plots) test still passes
  T7.  Phase-A2UI test still passes

Usage:
  source .venv/bin/activate
  uvicorn backend.main:app --port 8080 &
  python backend/scripts/test_phase_eda.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.tools import redis_state  # noqa: E402

WS_URL = "ws://127.0.0.1:8080/ws"
HEALTH_URL = "http://127.0.0.1:8080/healthz"
AGUI_URL = "http://127.0.0.1:8080/agui"
SID = f"eda-{int(time.time())}"

FAILURES: list[str] = []
WS_TIMEOUT = 60.0  # agent calls can take a few seconds


def _ok(msg: str) -> None: print(f"  PASS  {msg}")
def _bad(msg: str) -> None: print(f"  FAIL  {msg}"); FAILURES.append(msg)


# ---------------------------------------------------------------------------
async def _send_and_drain(payload: dict[str, Any], silence: float = 1.0) -> list[dict[str, Any]]:
    """Open /ws, send one payload, collect frames until `silence` of no traffic."""
    frames: list[dict[str, Any]] = []
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        await ws.send(json.dumps(payload))
        last = time.time()
        while time.time() - last < silence:
            try:
                f = await asyncio.wait_for(ws.recv(), timeout=silence)
                frames.append(json.loads(f))
                last = time.time()
                if isinstance(frames[-1], dict) and \
                   frames[-1].get("type") == "agent_status" and \
                   frames[-1].get("state") in ("done", "error"):
                    # tail-collect any straggler frame
                    try:
                        f2 = await asyncio.wait_for(ws.recv(), timeout=0.4)
                        frames.append(json.loads(f2))
                    except asyncio.TimeoutError:
                        pass
                    break
            except asyncio.TimeoutError:
                break
    return frames


async def _sse_collect(predicate, *, timeout: float = WS_TIMEOUT) -> list[dict[str, Any]]:
    """Subscribe to /agui and collect events; stop when `predicate(events)` returns True or timeout."""
    out: list[dict[str, Any]] = []
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", AGUI_URL, headers={"Accept": "text/event-stream"}) as resp:
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
def t1_healthz() -> None:
    print("\n=== T1. /healthz ===")
    r = httpx.get(HEALTH_URL, timeout=5.0).json()
    (_ok if r.get("contracts_version") == "0.0.2" else _bad)(
        f"contracts_version=0.0.2 (got {r.get('contracts_version')})"
    )
    (_ok if r.get("weave") else _bad)(f"weave init (got {r.get('weave')})")


# ---------------------------------------------------------------------------
async def t2_voice_query_titanic() -> None:
    print("\n=== T2. voice_query on titanic (default dataset) ===")

    sse_task = asyncio.create_task(_sse_collect(
        lambda evs: sum(1 for e in evs if e.get("name") == "beginRendering") >= 1
    ))
    await asyncio.sleep(0.5)

    t0 = time.time()
    frames = await _send_and_drain({
        "type": "voice_query", "session_id": SID,
        "text": "Show me the distributions and flag anything weird in the dataset.",
    }, silence=8.0)
    dt = time.time() - t0
    print(f"  ws latency: {dt:.1f}s, frames={len(frames)}")
    for f in frames:
        t = f.get("type")
        if t == "speech":
            print(f"    ← speech: {f.get('text')!r}")
        elif t == "panels":
            print(f"    ← panels: {len(f.get('panels', []))} "
                  f"({[p.get('kind') + ':' + str(p.get('column')) for p in f['panels']]})")
        else:
            print(f"    ← {t}  {f.get('state','') or ''}  {f.get('message','') or ''}")

    types = [f.get("type") for f in frames]
    (_ok if "speech" in types else _bad)(f"got speech frame (types={types})")
    panels = next((f for f in frames if f.get("type") == "panels"), None)
    (_ok if panels and panels.get("panels") else _bad)(
        f"got panels frame with ≥1 panel ({len(panels['panels']) if panels else 0})"
    )
    if panels:
        max_kb = max(len(p["image_b64"]) * 3 / 4 / 1024 for p in panels["panels"])
        (_ok if max_kb <= 80 else _bad)(f"all panel PNGs ≤80KB (largest={max_kb:.1f}KB)")
    speech = next((f["text"].lower() for f in frames if f.get("type") == "speech"), "")
    cited = [c for c in ("deck", "fare", "age", "sibsp", "embarked", "parch") if c in speech]
    (_ok if cited else _bad)(f"speech cites a real titanic column (cited={cited!r}, speech={speech[:120]!r})")

    sse_events = await asyncio.wait_for(sse_task, timeout=WS_TIMEOUT)
    surface_envs = [e for e in sse_events if e.get("event") == "CUSTOM" and
                    e.get("value", {}).get("surfaceUpdate", {}).get("surfaceId") == "eda-findings"]
    (_ok if surface_envs else _bad)(
        f"eda-findings surfaceUpdate emitted on /agui ({len(surface_envs)})"
    )
    data_envs = [e for e in sse_events if e.get("event") == "CUSTOM" and
                 e.get("value", {}).get("dataModelUpdate", {}).get("surfaceId") == "eda-findings"]
    if data_envs:
        contents = data_envs[0]["value"]["dataModelUpdate"]["contents"]
        first_key = contents[0].get("key") if contents else None
        (_ok if first_key == "findings" else _bad)(
            f"eda-findings dataModelUpdate top key='findings' (got {first_key})"
        )


async def t3_voice_query_sample() -> None:
    print("\n=== T3. switch to sample.csv, voice_query cites our synthetic columns ===")
    # 3a: load_dataset
    frames = await _send_and_drain({
        "type": "command", "session_id": SID,
        "action": "load_dataset", "params": {"name": "sample"},
    }, silence=2.0)
    last_status = next((f for f in reversed(frames) if f.get("type") == "agent_status"), {})
    (_ok if "shape" in (last_status.get("message") or "") else _bad)(
        f"load_dataset acknowledged (got {last_status})"
    )

    # 3b: voice_query against sample
    t0 = time.time()
    frames = await _send_and_drain({
        "type": "voice_query", "session_id": SID,
        "text": "Which columns are skewed or have outliers?",
    }, silence=8.0)
    dt = time.time() - t0
    print(f"  ws latency: {dt:.1f}s, frames={len(frames)}")
    speech = next((f["text"].lower() for f in frames if f.get("type") == "speech"), "")
    print(f"  speech: {speech[:160]!r}")
    cited = [c for c in ("price", "income", "balance", "tx_amount", "flag_v1") if c in speech]
    (_ok if cited else _bad)(f"speech cites a sample.csv column (cited={cited!r})")


async def t4_interaction_real_agent() -> None:
    print("\n=== T4. interaction select_point → real EDA agent → eda-action surface ===")

    # switch back to titanic for stable row IDs
    await _send_and_drain({
        "type": "command", "session_id": SID,
        "action": "load_dataset", "params": {"name": "titanic"},
    }, silence=2.0)

    sse_task = asyncio.create_task(_sse_collect(
        lambda evs: sum(1 for e in evs if e.get("name") == "beginRendering" and
                        e.get("value", {}).get("beginRendering", {}).get("surfaceId") == "eda-action") >= 1
    ))
    await asyncio.sleep(0.5)

    t0 = time.time()
    frames = await _send_and_drain({
        "type": "interaction", "session_id": SID,
        "action": "select_point", "point_ids": ["r10", "r25", "r80"],
    }, silence=10.0)
    dt = time.time() - t0
    print(f"  ws latency: {dt:.1f}s, frames={len(frames)}")
    for f in frames:
        if f.get("type") == "speech":
            print(f"    ← speech: {f.get('text')!r}")
        elif f.get("type") == "highlight":
            print(f"    ← highlight: {f.get('target_ids')} reason={f.get('reason')}")

    types = [f.get("type") for f in frames]
    (_ok if "speech" in types else _bad)("got speech")
    (_ok if "highlight" in types else _bad)("got highlight")

    sse_events = await asyncio.wait_for(sse_task, timeout=WS_TIMEOUT)
    action_envs = [e for e in sse_events if e.get("event") == "CUSTOM" and
                   e.get("value", {}).get("surfaceUpdate", {}).get("surfaceId") == "eda-action"]
    (_ok if action_envs else _bad)(
        f"eda-action surface emitted on /agui ({len(action_envs)})"
    )


def t5_redis_state() -> None:
    print("\n=== T5. redis has profile + findings for session ===")
    prof = redis_state.get_profile(SID)
    (_ok if prof else _bad)(f"session profile in redis (n_cols={prof.get('n_cols') if prof else None})")
    findings = redis_state.get_findings(SID)
    (_ok if findings else _bad)(f"session findings in redis ({len(findings)} rows)")
    if findings:
        for f in findings[:5]:
            print(f"    - {f.get('column'):<14} {f.get('flag'):<14} {f.get('note')}")


def t67_regressions() -> None:
    print("\n=== T6+T7. Phase-1 + Phase-A2UI regressions ===")
    for name, script in (("phase1", "backend/scripts/test_phase1.py"),
                         ("a2ui",   "backend/scripts/test_phase_a2ui.py")):
        env = dict(__import__("os").environ)
        env["MPLCONFIGDIR"] = "/tmp/mpl"
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, env=env, timeout=120)
        last = r.stdout.strip().split("\n")[-1] if r.stdout else ""
        (_ok if r.returncode == 0 else _bad)(
            f"{name} regression: exit={r.returncode}, last_line={last!r}"
        )


# ---------------------------------------------------------------------------
async def main() -> int:
    t1_healthz()
    await t2_voice_query_titanic()
    await t3_voice_query_sample()
    await t4_interaction_real_agent()
    t5_redis_state()
    t67_regressions()

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
