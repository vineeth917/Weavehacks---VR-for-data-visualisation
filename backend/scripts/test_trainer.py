#!/usr/bin/env python3
"""Test: trainer agent (ENABLE_TRAINER=1 + PREPROCESSOR + EVALS).

Full real loop on titanic + replay fallback when trainer flag off.

Usage:
    ENABLE_PREPROCESSOR=1 ENABLE_EVALS=1 ENABLE_TRAINER=1 uvicorn backend.main:app --port 8080 &
    ENABLE_PREPROCESSOR=1 ENABLE_EVALS=1 ENABLE_TRAINER=1 python backend/scripts/test_trainer.py
"""
from __future__ import annotations

import asyncio
import json
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
SID = f"trainer-{int(time.time())}"
FAILURES: list[str] = []


def _ok(m: str) -> None:
    print(f"  PASS  {m}")


def _bad(m: str) -> None:
    print(f"  FAIL  {m}")
    FAILURES.append(m)


def _slim(f: dict[str, Any]) -> dict[str, Any]:
    if f.get("type") == "training_update":
        return {
            "type": "training_update",
            "run_id": f.get("run_id"),
            "step": f.get("step"),
            "metrics": f.get("metrics"),
            "status": f.get("status"),
        }
    if f.get("type") == "panels":
        return {"type": "panels", "n": len(f.get("panels") or [])}
    return {k: f.get(k) for k in ("type", "agent", "state", "message", "text") if f.get(k) is not None}


async def ws_step(
    ws,
    payload: dict[str, Any],
    *,
    until_agent: str | None = None,
    label: str = "",
    collect_training: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    await ws.send(json.dumps({**payload, "session_id": SID}))
    frames: list[dict[str, Any]] = []
    training_frames: list[dict[str, Any]] = []
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        except asyncio.TimeoutError:
            break
        frames.append(f)
        if collect_training and f.get("type") == "training_update":
            training_frames.append(f)
        if f.get("type") == "agent_status" and f.get("state") in ("done", "error"):
            if until_agent is None or f.get("agent") == until_agent:
                break
    if label:
        print(f"\n--- {label} ---")
        for fr in frames:
            print(json.dumps(_slim(fr), ensure_ascii=False))
    return frames, training_frames


async def full_loop() -> dict[str, Any]:
    print("\n=== FULL LOOP (titanic, one WS session) ===")
    all_training: list[dict[str, Any]] = []
    monitor_source = ""
    evals_acc = None
    wandb_url = ""

    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        steps = [
            ({"type": "command", "action": "load_dataset", "params": {"name": "titanic"}}, "router", "load titanic", False),
            ({"type": "voice_query", "text": "what problem are we solving?"}, "problem_type", "problem_type", False),
            ({"type": "voice_query", "text": "show me the data — which columns are skewed or missing?"}, "eda", "eda", False),
            ({"type": "voice_query", "text": "remove nulls and duplicates"}, "preprocessor", "clean", False),
            ({"type": "voice_query", "text": "is my data ready to train?"}, "preprocessor", "ready?", False),
            ({"type": "voice_query", "text": "train the model"}, "trainer", "train", True),
            ({"type": "voice_query", "text": "is my model overfitting?"}, "training_monitor", "overfit?", False),
            ({"type": "voice_query", "text": "run the evals"}, "evals", "evals", False),
            ({"type": "voice_query", "text": "wrap up — narrate what we found"}, "narrator", "narrate", False),
        ]
        for payload, agent, label, collect in steps:
            fr, tu = await ws_step(ws, payload, until_agent=agent, label=label, collect_training=collect)
            if collect:
                all_training = tu

    from backend.tools import redis_state

    trainer_scratch = redis_state.get_scratch(SID, "trainer_run") or {}
    evals_scratch = redis_state.get_scratch(SID, "evals") or {}
    monitor_scratch = redis_state.get_scratch(SID, "training_run") or {}

    monitor_source = monitor_scratch.get("source", "")
    evals_acc = evals_scratch.get("accuracy")
    wandb_url = trainer_scratch.get("wandb_url") or ""

    from backend.tools.wandb_history import get_run_history

    (_ok if len(all_training) >= 10 else _bad)(f"training_update frames ({len(all_training)})")
    first = all_training[0] if all_training else {}
    last = all_training[-1] if all_training else {}
    (_ok if first.get("metrics", {}).get("train_loss") is not None else _bad)(
        f"first epoch real train_loss ({first.get('metrics')})"
    )
    (_ok if last.get("status") == "done" else _bad)(f"final training_update status ({last.get('status')})")
    (_ok if monitor_source == "trainer" else _bad)(
        f"training_monitor read REAL run source={monitor_source!r} run_id={monitor_scratch.get('run_id')!r}"
    )
    (_ok if trainer_scratch.get("metrics") and len(trainer_scratch["metrics"]) >= 10 else _bad)(
        f"trainer_run scratch epochs ({len(trainer_scratch.get('metrics') or [])})"
    )
    (_ok if evals_acc is not None and 0 <= evals_acc <= 1 else _bad)(
        f"evals real test accuracy ({evals_acc})"
    )

    replay_h = get_run_history("demo-overfit-001", session_id=f"no-train-{int(time.time())}")
    (_ok if replay_h.get("source") == "replay" else _bad)(
        f"replay fallback when no session trainer ({replay_h.get('source')})"
    )
    if trainer_scratch.get("run_id"):
        real_h = get_run_history(trainer_scratch["run_id"], session_id=SID)
        (_ok if real_h.get("source") == "trainer" else _bad)(
            f"get_run_history prefers trainer run ({real_h.get('source')})"
        )

    print(f"\n=== TRAINER SUMMARY ===")
    print(f"  training_update frames: {len(all_training)}")
    print(f"  sample frames (first 3):")
    for row in all_training[:3]:
        print(f"    {json.dumps(_slim(row))}")
    print(f"  sample frames (last 2):")
    for row in all_training[-2:]:
        print(f"    {json.dumps(_slim(row))}")
    print(f"  trainer_run source: {trainer_scratch.get('source')}")
    print(f"  trainer_run id: {trainer_scratch.get('run_id')}")
    print(f"  monitor source: {monitor_source}")
    print(f"  evals accuracy: {evals_acc}")
    print(f"  wandb_url: {wandb_url or '(local only)'}")

    return {
        "training_frames": all_training,
        "monitor_source": monitor_source,
        "evals_acc": evals_acc,
        "wandb_url": wandb_url,
        "trainer_scratch": trainer_scratch,
    }


async def replay_fallback() -> None:
    """With trainer flag off, training_monitor should use replay."""
    print("\n=== REPLAY FALLBACK (trainer flag off on server) ===")
    h = httpx.get(HEALTH_URL, timeout=5).json()
    if h.get("enable_trainer"):
        print("  SKIP  server has enable_trainer=true — restart without ENABLE_TRAINER for fallback test")
        return

    sid = f"replay-{int(time.time())}"
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        await ws.send(json.dumps({
            "type": "voice_query",
            "text": "is my model overfitting?",
            "session_id": sid,
        }))
        while True:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
            if f.get("type") == "agent_status" and f.get("agent") == "training_monitor" and f.get("state") == "done":
                break

    from backend.tools import redis_state
    scratch = redis_state.get_scratch(sid, "training_run") or {}
    src = scratch.get("source", "")
    (_ok if src == "replay" else _bad)(f"fallback source={src!r} run={scratch.get('run_id')!r}")


async def main() -> int:
    h = httpx.get(HEALTH_URL, timeout=5).json()
    (_ok if h.get("enable_trainer") else _bad)("server ENABLE_TRAINER=1")
    (_ok if h.get("enable_preprocessor") else _bad)("server ENABLE_PREPROCESSOR=1")
    (_ok if h.get("enable_evals") else _bad)("server ENABLE_EVALS=1")

    await full_loop()

    print("\n=== SUMMARY ===")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL GREEN (trainer loop)")
    print("NOTE: restart server WITHOUT ENABLE_TRAINER and re-run replay_fallback() manually")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
