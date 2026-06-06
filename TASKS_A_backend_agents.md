# TASKS_A — Backend orchestrator & agent swarm (ML lead)

> Read `PLAN.md` first. You own everything server-side: the agent swarm, tools, Weave tracing, CoreWeave inference routing, Redis state, and the WS + AG-UI endpoints. You are the integration anchor — your `contracts.py` is the law.
> **Feed `PLAN.md` + this file to your Cursor agent. Work in `backend/`.**

## Stack
Python 3.12 · FastAPI + uvicorn · `openai-agents` (Agents SDK) · `weave` · `redis` · `pandas`/`numpy` · `matplotlib` (panel PNGs) · `wandb`.

## ✅ Local Mac prerequisites — check BEFORE you start Cursor
- [ ] Homebrew installed.
- [ ] **Python 3.12** (`brew install python@3.12` or pyenv); create a venv (`python3.12 -m venv .venv && source .venv/bin/activate`).
- [ ] **Redis running locally** — `brew install redis && redis-server`, or `docker run -p 6379:6379 redis`. Confirm `redis-cli ping` → `PONG`.
- [ ] **git + GitHub auth** — `gh auth login` (or SSH key). Confirm you can create a repo and push.
- [ ] **Cursor** installed, logged in, **Opus model available**.
- [ ] **API keys exported** (in `.env`, never committed): `OPENAI_API_KEY`, `WANDB_API_KEY`, `REDIS_URL=redis://localhost:6379`, `WANDB_INFERENCE_BASE=https://api.inference.wandb.ai/v1`.
- [ ] **`wandb login`** done; Weave project accessible.
- [ ] **CoreWeave inference reachable + credits live** — `curl https://api.inference.wandb.ai/v1/models -H "Authorization: Bearer $WANDB_API_KEY"` returns models. If slow/blocked at the venue, plan to fall back to OpenAI now.
- [ ] **A WS test client** so you don't wait on B: `npm i -g wscat` (or a 10-line python `websockets` script). Test `/ws` solo.
- [ ] (Node is **not** needed for backend — that's only Person C.)

## Repo & git
- Create **your own repo** for the backend (`hololab-backend`). Three separate repos = zero merge conflicts; you integrate at runtime over WS/AG-UI, not via shared code.
- The only thing you share outward is the **§6 schema**: after writing `contracts.py`, paste the JSON shapes into a `CONTRACTS.md` (or drop the mock messages in a `mocks/` folder) and hand that — plus `PLAN.md` — to B and C. **They never need your repo or `TASKS_A`.**
- **Schema freeze rule:** once you publish `contracts.py`, any change to §6 is a stop-and-broadcast event. Silent schema drift is the #1 way to break B and C.
- Brief your Cursor agent: *"Read PLAN.md and TASKS_A. Work the `## Task checklist` top to bottom. After each task: check its box `- [x]`, commit with a clear message, and push. Do not edit folders other than `backend/`. Treat `contracts.py` / PLAN §6 as frozen — flag me before changing any schema."*

## Dependencies / how to unblock yourself
- B and C build against your §6 schemas. **Ship `contracts.py` + a mock WS server in the first hour** so they can start.
- Until the training plane exists, the training-monitor reads `data/replay_run_history.json`.

## Task checklist
- [ ] **(0–1h)** Scaffold `main.py`: FastAPI, `/ws` WebSocket echo, `/agui` stub. `weave.init("hololab")`. Connect Redis. Write `contracts.py` (pydantic models for all §6 messages). Commit + tell B/C it's ready.
- [ ] **(0–1h)** `tools/redis_state.py`: get/set helpers for the §6.5 keys.
- [ ] **(1–3h)** `tools/profiling.py`: `profile_dataset(df)` → schema, dtypes, missing %, skew, outlier % (IQR), top correlations. `tools/plots.py`: `render_plot(df, col, kind)` → base64 PNG (histogram/box/kde/corr/missing-matrix). Keep panels flat 2D.
- [ ] **(1–4h)** `agents/eda.py` (OpenAI Agents SDK): EDA agent with tools `profile_dataset`, `render_plot`, `flag_columns`. Input = NL query + schema; output = `panels` message + `speech`. Wrap every LLM/tool call in `@weave.op()`. Write findings to Redis.
- [ ] **(3–4h)** `agents/router.py`: routes a query to EDA / training-monitor / narrator via **handoffs** (per the Kundel gist). Emit `HANDOFF` AG-UI events.
- [ ] **(4–6h)** `tools/wandb_history.py`: `get_run_history(run_id)` from `wandb` API *or* `data/replay_run_history.json`. `agents/training_monitor.py`: `analyze_curve` → verdict on overfitting / early-stop / leakage with **reasons that cite the actual metric series**. Emit `training_update` messages on a timer to simulate streaming.
- [ ] **(6–7h)** `agents/narrator.py`: turn eval results into a `report` message with `speak:true`.
- [ ] **(by 6h INTEGRATION 1)** Real `voice_query` → EDA → `panels` over WS, end to end with B.
- [ ] **(7–9h)** Route agent LLM calls through CoreWeave inference:
  ```python
  client = openai.OpenAI(base_url="https://api.inference.wandb.ai/v1", api_key=os.environ["WANDB_API_KEY"])
  # model="nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B" or a Qwen3.5 variant
  ```
  Weave auto-traces the OpenAI client. Fall back to `OPENAI_API_KEY` if latency/limits bite.
- [ ] **(9–11h)** `/agui` endpoint: emit AG-UI events (`RUN_STARTED`, `TOOL_CALL_*`, `HANDOFF`, `STATE_DELTA`, `RUN_FINISHED`) so C's CopilotKit `HttpAgent` can subscribe.
- [ ] **(stretch)** `scripts/train_baseline.py`: tiny real run logging to W&B as background insurance.

## Definition of done
A WS client can send `voice_query`/`command`, and you stream back correct `panels`, `scatter3d`, `training_update`, `report`, `agent_status`. Every LLM/tool call appears in Weave. `/agui` emits a live event stream. Redis holds session truth.

## Watch out
- Keep panel PNGs small (≤~80KB) — base64 over WS adds up.
- Don't block the event loop on pandas/matplotlib — run in a thread executor.
- `analyze_curve` must reference real numbers, not vibes — judges will check.
