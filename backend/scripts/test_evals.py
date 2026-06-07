#!/usr/bin/env python3
"""Test: evals agent (ENABLE_EVALS=1 + ENABLE_PREPROCESSOR=1).

Full session loop ending with evals + narrate; regression suites flag OFF/ON.

Usage:
    ENABLE_PREPROCESSOR=1 ENABLE_EVALS=1 uvicorn backend.main:app --port 8080 &
    ENABLE_PREPROCESSOR=1 ENABLE_EVALS=1 python backend/scripts/test_evals.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WS_URL = "ws://127.0.0.1:8080/ws"
HEALTH_URL = "http://127.0.0.1:8080/healthz"
SID = f"evals-{int(time.time())}"
FAILURES: list[str] = []


def _ok(m: str) -> None:
    print(f"  PASS  {m}")


def _bad(m: str) -> None:
    print(f"  FAIL  {m}")
    FAILURES.append(m)


def _slim(f: dict[str, Any]) -> dict[str, Any]:
    if f.get("type") == "panels":
        return {
            "type": "panels",
            "panels": [
                {
                    "id": p.get("id"),
                    "kind": p.get("kind"),
                    "title": p.get("title"),
                    "image_b64_len": len(p.get("image_b64") or ""),
                }
                for p in f.get("panels", [])
            ],
        }
    if f.get("type") == "report":
        return {"type": "report", "verdict": f.get("verdict"), "sections": len(f.get("sections") or [])}
    return {k: f.get(k) for k in ("type", "agent", "state", "message", "text") if f.get(k) is not None}


async def ws_step(ws, payload: dict[str, Any], *, until_agent: str | None = None,
                  label: str = "") -> list[dict[str, Any]]:
    await ws.send(json.dumps({**payload, "session_id": SID}))
    frames: list[dict[str, Any]] = []
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        except asyncio.TimeoutError:
            break
        frames.append(f)
        if f.get("type") == "agent_status" and f.get("state") in ("done", "error"):
            if until_agent is None or f.get("agent") == until_agent:
                break
    if label:
        print(f"\n--- {label} ---")
        for fr in frames:
            print(json.dumps(_slim(fr), ensure_ascii=False))
    return frames


async def full_loop() -> dict[str, Any]:
    print("\n=== FULL LOOP (one WS session) ===")
    evals_frames: list[dict[str, Any]] = []
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        steps = [
            ({"type": "command", "action": "load_dataset", "params": {"name": "sample"}}, "router", "load sample"),
            ({"type": "voice_query", "text": "what problem are we solving?"}, "problem_type", "problem_type"),
            ({"type": "voice_query", "text": "show me the data — which columns are skewed or missing?"}, "eda", "eda"),
            ({"type": "voice_query", "text": "remove nulls and duplicates and log-transform the skewed columns"}, "preprocessor", "clean"),
            ({"type": "voice_query", "text": "show me the data again"}, "eda", "eda again"),
            ({"type": "voice_query", "text": "is my data ready to train?"}, "preprocessor", "ready?"),
            ({"type": "voice_query", "text": "run the evals"}, "evals", "evals"),
            ({"type": "voice_query", "text": "wrap up — narrate what we found"}, "narrator", "narrate"),
        ]
        for payload, agent, label in steps:
            fr = await ws_step(ws, payload, until_agent=agent, label=label)
            if agent == "evals":
                evals_frames = fr

    from backend.agents import dataset_versions as dv

    ver = dv.current_version(SID)
    from backend.tools import redis_state
    scratch = redis_state.get_scratch(SID, "evals")

    speech = next((f.get("text") for f in evals_frames if f.get("type") == "speech"), "")
    status = next((f for f in evals_frames if f.get("agent") == "evals"), {})
    panels = next((f for f in evals_frames if f.get("type") == "panels"), {})

    (_ok if speech else _bad)(f"evals speech ({speech!r})")
    (_ok if "accuracy" in speech.lower() or "rmse" in speech.lower() else _bad)(
        f"speech cites real test metrics ({speech!r})"
    )
    (_ok if panels and panels.get("panels") else _bad)(f"evals panels ({panels})")
    (_ok if ver is not None and ver >= 1 else _bad)(f"dataset version after clean (v{ver})")
    acc = (scratch or {}).get("accuracy")
    (_ok if acc is not None and 0 <= acc <= 1 else _bad)(f"REAL test accuracy in scratch ({acc})")
    cm = (scratch or {}).get("confusion_matrix")
    (_ok if cm and len(cm) >= 2 else _bad)(f"REAL confusion matrix ({cm})")

    print(f"\n=== EVALS SUMMARY ===")
    print(f"  version read: v{scratch.get('version') if scratch else ver}")
    print(f"  target: {scratch.get('target_column') if scratch else '?'}")
    print(f"  test accuracy: {acc}")
    print(f"  confusion_matrix: {cm}")
    print(f"  agent_status: {status.get('message')!r}")

    return {"speech": speech, "accuracy": acc, "cm": cm, "version": scratch.get("version") if scratch else ver}


def regressions(flag_on: bool) -> None:
    label = "ON" if flag_on else "OFF"
    print(f"\n=== REGRESSIONS (flags {label}) ===")
    env = dict(os.environ)
    env["MPLCONFIGDIR"] = "/tmp/mpl"
    if flag_on:
        env["ENABLE_PREPROCESSOR"] = "1"
        env["ENABLE_EVALS"] = "1"
    else:
        env.pop("ENABLE_PREPROCESSOR", None)
        env.pop("ENABLE_EVALS", None)
    for name, script in (
        ("phase1", "backend/scripts/test_phase1.py"),
        ("a2ui", "backend/scripts/test_phase_a2ui.py"),
        ("eda", "backend/scripts/test_phase_eda.py"),
        ("router", "backend/scripts/test_phase_router.py"),
        ("problem_type", "backend/scripts/test_problem_type.py"),
        ("preprocessor", "backend/scripts/test_preprocessor.py"),
    ):
        r = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, env=env, timeout=600,
        )
        last = r.stdout.strip().split("\n")[-1] if r.stdout else ""
        if name == "preprocessor" and not flag_on:
            (_ok if r.returncode == 0 or "enable_preprocessor=false" in (r.stdout or "") else _bad)(
                f"[{label}] {name}: exit={r.returncode}"
            )
            continue
        (_ok if r.returncode == 0 else _bad)(
            f"[{label}] {name}: exit={r.returncode}, last={last!r}"
        )


async def main() -> int:
    h = httpx.get(HEALTH_URL, timeout=5).json()
    (_ok if h.get("enable_evals") else _bad)("server ENABLE_EVALS=1")
    (_ok if h.get("enable_preprocessor") else _bad)("server ENABLE_PREPROCESSOR=1")

    await full_loop()
    regressions(flag_on=False)
    regressions(flag_on=True)

    print("\n=== SUMMARY ===")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
