# hololab-backend (TASKS_A)

FastAPI + WebSocket orchestrator for the HoloLab multi-agent swarm.
See [`../PLAN.md`](../PLAN.md) and [`../TASKS_A_backend_agents.md`](../TASKS_A_backend_agents.md).

## Quick start

```bash
# 1. venv (Python 3.12)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# 2. env (copy from .env.example at repo root, fill keys)
cp .env.example .env

# 3. start Redis (separate terminal)
brew services start redis  # or: redis-server

# 4. run backend
python -m backend.main
# or: uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

## Smoke tests

```bash
# Liveness
curl -s http://localhost:8080/healthz | python -m json.tool

# WebSocket echo (needs wscat: npm i -g wscat)
echo '{"type":"voice_query","session_id":"s1","text":"hello"}' | wscat -c ws://localhost:8080/ws

# AG-UI SSE heartbeat
curl -N http://localhost:8080/agui

# Or use the bundled test scripts
python scripts/smoke_ws.py
```

## Model selection

Picked from the bench in `scripts/bench_models.py`:

| Role | Model | p50 latency |
|---|---|---|
| Router & Reasoning | `Qwen/Qwen3-235B-A22B-Instruct-2507` | 0.33–0.56s |
| Deep reasoning (fallback) | `nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B` | 0.75s |
| Offline fallback | `gpt-4o-mini` (OpenAI) | 1.3s |

Override at runtime via env: `ROUTER_MODEL`, `REASONING_MODEL`, `DEEP_REASONING_MODEL`, `FALLBACK_MODEL`.

## Layout

```
backend/
├── main.py                # FastAPI app, /healthz, /ws, /agui
├── contracts.py           # pydantic schemas (PLAN §6) — FROZEN
├── config.py              # env + model selection
├── agents/                # router, eda, training_monitor, narrator (filled per phase)
├── tools/
│   ├── redis_state.py     # §6.5 session keys
│   ├── profiling.py       # phase 1
│   ├── plots.py           # phase 1
│   └── wandb_history.py   # phase 4
├── mocks/                 # sample messages for B & C
└── CONTRACTS.md           # human-readable schema (for B & C)
```
