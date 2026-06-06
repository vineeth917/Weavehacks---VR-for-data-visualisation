#!/usr/bin/env python3
"""WebSocket smoke test against /ws. Run after `uvicorn backend.main:app`."""
import asyncio
import json
import sys

import websockets

URL = "ws://127.0.0.1:8080/ws"

MESSAGES = [
    {"type": "voice_query", "session_id": "s1", "text": "hello swarm"},
    {"type": "command", "session_id": "s1", "action": "load_dataset", "params": {"path": "data/iris.csv"}},
    {"type": "interaction", "session_id": "s1", "action": "select_panel", "target_id": "price_hist"},
    {"type": "bogus", "oops": True},  # should round-trip an error
]


async def main() -> int:
    try:
        async with websockets.connect(URL, open_timeout=5) as ws:
            for m in MESSAGES:
                await ws.send(json.dumps(m))
                print(f"\n→ {m}")
                # collect frames until 0.3s of silence
                while True:
                    try:
                        reply = await asyncio.wait_for(ws.recv(), timeout=0.3)
                    except asyncio.TimeoutError:
                        break
                    print(f"  ← {reply}")
            return 0
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
