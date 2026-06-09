# DataDive

**Stand inside your data and talk to the agents doing your data science.**

Built at **WeaveHacks 4** (Weights & Biases × CoreWeave) — a multi-agent VR data science system. Put on a Meta Quest, speak to a swarm of AI specialists, and watch the same pipeline live on a spectator dashboard.

## What it does

DataDive turns tabular datasets into an immersive room you can explore in VR. You talk naturally — *“which columns are skewed?”*, *“clean the data”*, *“train the model”*, *“is it overfitting?”* — and specialized agents respond in real time with speech, floating stat panels, training curves, and a spoken session recap. A 2D dashboard mirrors every agent handoff, tool call, and pipeline frame for judges and remote teammates.

## Architecture

<img width="1376" height="768" alt="Gemini_Generated_Image_qgy4moqgy4moqgy4 (1)" src="https://github.com/user-attachments/assets/03084ef8-47d6-44bd-b399-c2ac157b081d" />

```
Meta Quest (WebXR)          Spectator dashboard (Next.js + CopilotKit)
     │  voice / WS                    │  SSE /agui
     └──────────────┬─────────────────┘
                    ▼
         FastAPI backend (Python)
    Router → EDA · Preprocessor · Trainer · Evals · Narrator
         │              │              │
    Redis state    W&B Weave      matplotlib panels
```

## Monorepo layout

| Folder | Owner | What it is |
|--------|-------|------------|
| [`backend/`](backend/) | Person A | FastAPI orchestrator, agent swarm, `/ws` + `/agui`, Redis, Weave tracing |
| [`vr-client/`](vr-client/) | Person B | Meta Quest WebXR client — panels, 3D scatter, loss ribbon, voice |
| [`dashboard/`](dashboard/) | Person C | CopilotKit spectator dashboard — live swarm timeline, loss chart, A2UI |
| [`data/`](data/) | Person C | Demo CSVs + replay training histories |
| [`scripts/`](scripts/) | Person C | `ngrok.sh`, `adb_reverse.sh`, tunnel helpers |

**Interface contracts:** [`backend/CONTRACTS.md`](backend/CONTRACTS.md) · port **8080** · `WS /ws` · `GET /agui`

## Partner technologies

| Partner | How we use it |
|---------|----------------|
| **OpenAI Agents SDK** | Multi-agent router with handoffs to EDA, trainer, narrator, etc. |
| **W&B Weave** | `@weave.op()` tracing on every agent and tool call |
| **CoreWeave inference** | LLM routing via W&B inference API (Qwen3 / Nemotron) |
| **Redis** | Shared session state — profiles, findings, per-agent scratch |
| **CopilotKit / AG-UI** | Spectator dashboard SSE stream + A2UI surface rendering |
| **wandb** | Live sklearn training logs and run history |
| **Cursor** | Built with Cursor agents from `TASKS_A/B/C` spec files |

## Quick start

### 1. Backend (required)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env   # fill OPENAI_API_KEY, WANDB_API_KEY
redis-server           # or docker run -p 6379:6379 redis

source .venv/bin/activate && set -a && source .env && set +a
ENABLE_PREPROCESSOR=1 ENABLE_EVALS=1 ENABLE_TRAINER=1 \
  uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

Dev UI: http://localhost:8080/dev-ui

### 2. Dashboard (spectator)

```bash
cd dashboard && npm install
NEXT_PUBLIC_BACKEND_URL=http://localhost:8080 npm run dev
```

Open http://localhost:3000

### 3. VR client (Meta Quest)

```bash
./scripts/adb_reverse.sh   # USB Quest → localhost:8080
# Open http://localhost:8080 in Quest browser (or serve vr-client/)
```

### Remote access (ngrok)

```bash
./scripts/ngrok.sh   # prints public HTTPS/WSS URLs for remote teammates
```

See [`DEMO_LAUNCH.md`](DEMO_LAUNCH.md) for the full 9-step demo script (titanic classification).

## Demo pipeline (voice)

1. Load dataset → 2. Problem type → 3. EDA → 4. Clean → 5. Make ready → 6. Readiness check → 7. Train → 8. Overfitting check → 9. Evals + narrate

## Git branches

| Branch | Status |
|--------|--------|
| **`main`** | ✅ **Canonical** — backend + VR client + dashboard merged |
| `feature/dashboard` | Merged into `main` (Person C + integrated Person B) |
| `task-b` | Merged via `feature/dashboard` (Person B VR client) |
| `vineeth` | Early backend scaffold (superseded by `main`) |

## Team

3-person WeaveHacks team — parallel build against frozen `contracts.py`, integrated at runtime over WebSocket and AG-UI (no shared app code between folders).

## Links

- **Repo:** https://github.com/vineeth917/Weavehacks---VR-for-data-visualisation
- **Backend contracts:** [`backend/CONTRACTS.md`](backend/CONTRACTS.md)
- **Master plan:** [`PLAN.md`](PLAN.md)

---

*Internal codename: HoloLab. Documentation name: **DataDive**.*
