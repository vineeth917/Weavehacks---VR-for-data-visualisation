#!/usr/bin/env python3
"""Test: preprocessor agent (ENABLE_PREPROCESSOR=1).

Asserts:
  R1. sample.csv cleanup voice_query → new version, v0 intact, speech + panels,
      skew lower after log-transform.
  R2. "is my data ready to train?" → read-only readiness verdict.
  R3. Regressions with flag OFF then ON.

Usage:
    ENABLE_PREPROCESSOR=1 uvicorn backend.main:app --host 0.0.0.0 --port 8080 &
    ENABLE_PREPROCESSOR=1 python backend/scripts/test_preprocessor.py
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
from typing import Any

import httpx
import pandas as pd
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.tools.profiling import profile_dataset  # noqa: E402

WS_URL = "ws://127.0.0.1:8080/ws"
HEALTH_URL = "http://127.0.0.1:8080/healthz"
SID = f"prep-{int(time.time())}"

FAILURES: list[str] = []
WS_TIMEOUT = 90.0


def _ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def _bad(msg: str) -> None:
    print(f"  FAIL  {msg}")
    FAILURES.append(msg)


def _skew_for(df: pd.DataFrame, col: str) -> float:
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    return float(s.skew()) if len(s) >= 3 else 0.0


async def ws_collect(
    payload: dict[str, Any],
    *,
    until_agent: str = "preprocessor",
    hard_cap: float = 90.0,
    silence: float = 20.0,
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    deadline = time.time() + hard_cap
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        await ws.send(json.dumps(payload))
        while time.time() < deadline:
            try:
                f = await asyncio.wait_for(ws.recv(), timeout=silence)
                frames.append(json.loads(f))
                if (frames[-1].get("type") == "agent_status"
                        and frames[-1].get("agent") == until_agent
                        and frames[-1].get("state") in ("done", "error")):
                    break
            except asyncio.TimeoutError:
                break
    return frames


async def r1_cleanup_transform() -> dict[str, Any]:
    print("\n=== R1. cleanup transform on sample.csv ===")
    os.environ["ENABLE_PREPROCESSOR"] = "1"

    await ws_collect(
        {"type": "command", "session_id": SID,
         "action": "load_dataset", "params": {"name": "sample"}},
        until_agent="router", hard_cap=10.0, silence=3.0,
    )

    sample_path = ROOT / "data" / "sample.csv"
    v0_df = pd.read_csv(sample_path)
    skew_before = max(_skew_for(v0_df, "price"), _skew_for(v0_df, "income"))

    frames = await ws_collect(
        {"type": "voice_query", "session_id": SID,
         "text": ("remove nulls and duplicates and log-transform "
                  "the skewed columns")},
    )

    for f in frames:
        if f.get("type") == "speech":
            print(f"    ← speech: {f.get('text')!r}")
        elif f.get("type") == "panels":
            print(f"    ← panels: {len(f.get('panels', []))}")
        elif f.get("type") == "agent_status":
            print(f"    ← {f.get('agent')}/{f.get('state')}: {f.get('message')!r}")

    from backend.agents import dataset_versions  # noqa: E402

    v0_redis = dataset_versions.get_v0(SID)
    working = dataset_versions.get_working(SID)
    ver = dataset_versions.current_version(SID)

    (_ok if v0_redis is not None and len(v0_redis) == len(v0_df) else _bad)(
        f"v0 preserved in Redis ({len(v0_redis) if v0_redis is not None else 0} rows)"
    )
    (_ok if ver is not None and ver >= 1 else _bad)(
        f"new version created (current={ver})"
    )
    (_ok if working and working[0] == ver else _bad)(
        f"working copy at v{working[0] if working else None}"
    )

    speech = next((f.get("text", "") for f in frames if f.get("type") == "speech"), "")
    (_ok if speech else _bad)(f"speech frame returned ({speech!r})")
    (_ok if re.search(r"log|drop|null|duplicate|skew", speech, re.I) else _bad)(
        f"speech cites changes (got {speech!r})"
    )

    if working:
        skew_after = max(_skew_for(working[2], "price"), _skew_for(working[2], "income"))
        (_ok if skew_after < skew_before else _bad)(
            f"skew lower after transform ({skew_before:.2f} → {skew_after:.2f})"
        )
    else:
        _bad("no working df for skew compare")

    panels = next((f for f in frames if f.get("type") == "panels"), None)
    (_ok if panels and len(panels.get("panels", [])) >= 1 else _bad)(
        f"panels frame with ≥1 panel (got {panels})"
    )

    return {"frames": frames, "skew_before": skew_before}


async def r2_readiness_check() -> None:
    print("\n=== R2. readiness check (read-only) ===")
    frames = await ws_collect(
        {"type": "voice_query", "session_id": SID,
         "text": "is my data ready to train?"},
    )
    speech = next((f.get("text", "") for f in frames if f.get("type") == "speech"), "")
    done = next(
        (f for f in frames if f.get("type") == "agent_status"
         and f.get("agent") == "preprocessor" and f.get("state") == "done"),
        {},
    )
    print(f"    ← speech: {speech!r}")
    print(f"    ← status: {done.get('message')!r}")

    (_ok if speech else _bad)("readiness speech returned")
    (_ok if re.search(r"ready|not ready|skew|missing|imbalance", speech, re.I) else _bad)(
        f"verdict language in speech (got {speech!r})"
    )
    msg = (done.get("message") or "").lower()
    (_ok if "mode=readiness" in msg or "ready=" in msg else _bad)(
        f"agent_status cites readiness mode (got {msg!r})"
    )


def r3_regressions(flag_on: bool) -> None:
    label = "ON" if flag_on else "OFF"
    print(f"\n=== R3. regressions (ENABLE_PREPROCESSOR={label}) ===")
    env = dict(os.environ)
    env["MPLCONFIGDIR"] = "/tmp/mpl"
    if flag_on:
        env["ENABLE_PREPROCESSOR"] = "1"
    else:
        env.pop("ENABLE_PREPROCESSOR", None)

    for name, script in (
        ("phase1", "backend/scripts/test_phase1.py"),
        ("a2ui", "backend/scripts/test_phase_a2ui.py"),
        ("eda", "backend/scripts/test_phase_eda.py"),
        ("router", "backend/scripts/test_phase_router.py"),
        ("problem_type", "backend/scripts/test_problem_type.py"),
    ):
        r = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, env=env, timeout=600,
        )
        last = (r.stdout.strip().split("\n")[-1] if r.stdout else "")
        (_ok if r.returncode == 0 else _bad)(
            f"[{label}] {name}: exit={r.returncode}, last={last!r}"
        )


async def main() -> int:
    h = httpx.get(HEALTH_URL, timeout=5.0).json()
    if not h.get("enable_preprocessor"):
        _bad("server /healthz enable_preprocessor=false — restart with ENABLE_PREPROCESSOR=1")
        return 1
    _ok("server has ENABLE_PREPROCESSOR=1")

    await r1_cleanup_transform()
    await r2_readiness_check()
    r3_regressions(flag_on=False)
    r3_regressions(flag_on=True)

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
