# HoloLab — WeaveHacks 4 Master Plan (shared source of truth)

> Codename "HoloLab" (rename freely). **Tagline: "Stand inside your data and talk to the agents doing your data science."**
> Read this file FIRST, then your role file (`TASKS_A`, `TASKS_B`, or `TASKS_C`). Build against the **Interface Contracts** in §6 so all three of us can work in parallel without blocking.

Event: WeaveHacks 4 · June 6–7 2026 · W&B (by CoreWeave) SF office · **Submissions due Sun 1:00 PM.**
Team size: 3. Theme: **Multi-Agent Orchestration.**

---

## 1. The idea (one paragraph)
A human puts on a Meta Quest and stands inside a room of their own data. They **talk** to a swarm of agents that do the data science *in front of their eyes*: an EDA agent profiles the dataset and floats histogram / box / KDE / missingness panels around you (highlighting skew, missing values, outliers); a 3D scatter / embedding projection you can walk around; a training-monitor agent streams the live train/val loss as a ribbon you walk along and answers "is this overfitting? should I stop? is there leakage?" by reasoning over the real W&B trace; and a narrator agent presents the final eval report by voice. Every agent decision is traced in W&B Weave, and a 2D **spectator dashboard** (CopilotKit / AG-UI) mirrors the swarm thinking live on the projector during the pitch.

## 2. Why this wins
- **On-theme:** a human embedded in a coordinating multi-agent swarm = literal multi-agent orchestration.
- **Host alignment:** train/val curves + Weave are W&B's identity — the judges live in that screen.
- **Sponsor sweep:** W&B (Weave + CoreWeave inference) · OpenAI (Agents SDK) · Redis (shared agent memory) · CopilotKit/AG-UI (spectator dashboard → **AirPods Max**) · Cursor (we build with it).
- **Defensible:** we're ML engineers — the agents' analysis is *correct*, and we can field judge questions cold.
- **Demo moment:** judge speaks a custom query, the room rebuilds, an agent calls "stop, that's overfitting" on a live curve.

## 3. Scope — LOCKED
**IN (hero loop):**
1. Load a pre-staged tabular dataset (CSV).
2. Voice/controller query → EDA agent → floating stat panels + flagged columns.
3. 3D scatter / embedding projection you can walk around.
4. "Train a baseline" → training-monitor agent streams loss ribbon, answers overfitting/leakage questions over the real W&B run.
5. Narrator agent speaks the final eval report.
6. AG-UI spectator dashboard mirrors the swarm.

**OUT (do not build):**
- Image / HTML / arbitrary-file EDA → **CSV + simple JSON only**.
- 3D-ifying 2D stats → histograms/box/KDE stay **flat panels** arranged in space; reserve true 3D for scatter + embeddings + loss ribbon.
- **No live GPU training in the demo path** → drive from W&B run history (replay/stream); keep one tiny real run as background insurance only.
- Hand-tracking is a **stretch**; controllers are primary.
- OpenAI Realtime voice is a **stretch**; Web Speech API is primary.

## 4. Architecture
```
        Meta Quest (WebXR / Three.js)                 Projector
        ┌───────────────────────────┐        ┌─────────────────────────┐
        │  VR client (Person B)      │        │ Spectator dashboard (C) │
        │  - stat panels (2D)        │        │ Next.js + CopilotKit    │
        │  - 3D scatter / embeddings │        │ renders AG-UI events    │
        │  - loss ribbon             │        │ (swarm thinking live)   │
        │  - Web Speech STT/TTS      │        └───────────▲─────────────┘
        └─────────────▲─────────────┘                    │ AG-UI events (SSE/WS)
                      │ WebSocket (scene/state/speech)    │
                      │                                   │
        ┌─────────────┴───────────────────────────────────┴─────────────┐
        │  Backend orchestrator (Person A) — FastAPI + WebSocket (Python)│
        │  OpenAI Agents SDK swarm:                                      │
        │    Router → [EDA agent] [Training-monitor agent] [Narrator]    │
        │  LLM calls → W&B/CoreWeave inference (Nemotron 3 / Qwen3.5)    │
        │             or OpenAI · all @weave.op() traced                 │
        └───────┬───────────────────────┬───────────────────────┬───────┘
                │ pandas/numpy           │ wandb run history     │ Redis
        ┌───────▼────────┐      ┌────────▼─────────┐    ┌────────▼─────────┐
        │ data plane     │      │ training plane   │    │ Redis (shared    │
        │ EDA compute,   │      │ tiny real run +  │    │ state: profile,  │
        │ plot rendering │      │ replay history   │    │ findings, memory)│
        └────────────────┘      └──────────────────┘    └──────────────────┘
```

## 5. Agent topology (OpenAI Agents SDK, Python)
| Agent | Role | Tools | Model |
|---|---|---|---|
| **Router** | Receives NL query, routes / hands off | — (handoffs) | small/fast (OpenAI or Qwen3.5) |
| **EDA agent** | describe/info, skew, missing %, outliers, correlations → panel specs + findings | `profile_dataset`, `render_plot`, `flag_columns` | Nemotron 3 / OpenAI |
| **Training-monitor** | Reads W&B run history; verdict on overfitting / early-stop / leakage with reasons | `get_run_history`, `analyze_curve` | reasoning model (Nemotron 3) |
| **Narrator ("Santa")** | Turns evals into a spoken report | `get_eval_results` | any; voice = client TTS |

Delegation pattern follows the OpenAI gist: **specialization** (each agent restricted) + **delegation/handoff** (router → specialist). Emit AG-UI events on every handoff/tool call so the dashboard shows orchestration.

## 6. Interface contracts (BUILD AGAINST THESE — they unblock parallel work)
All three components agree on these schemas. Mock the others until they're live.

### 6.1 WebSocket — VR client → backend
```json
{ "type": "voice_query", "session_id": "s1", "text": "which columns are skewed?" }
{ "type": "command", "session_id": "s1", "action": "load_dataset|train_baseline|run_evals|reset", "params": {} }
{ "type": "interaction", "session_id": "s1", "action": "select_panel|select_point", "target_id": "price_hist" }
```

### 6.2 WebSocket — backend → VR client
```json
{ "type": "speech",  "agent": "eda", "text": "Three columns are right-skewed: price, income, balance." }
{ "type": "panels",  "panels": [
  { "id":"price_hist", "kind":"histogram|box|kde|corr|missing", "title":"Price",
    "column":"price", "image_b64":"<png>", "position_hint":"left", "flags":["right_skewed","outliers"] }
] }
{ "type": "scatter3d", "title":"Embedding projection",
  "axes": {"x":"PC1","y":"PC2","z":"PC3"},
  "points": [ { "id":"r0","x":0.1,"y":-0.4,"z":0.7,"color":"#3cb371","size":0.04,"shape":"sphere","label":"row 0" } ] }
{ "type": "training_update", "run_id":"r1", "step":120,
  "metrics": {"train_loss":0.21,"val_loss":0.33}, "status":"running|stopped|done" }
{ "type": "highlight", "target_ids":["price_hist"], "reason":"skew" }
{ "type": "report", "speak": true, "verdict":"Model is mildly overfitting after step 90.",
  "sections":[ {"title":"Metrics","body":"val_loss diverges from train_loss..."} ] }
{ "type": "agent_status", "agent":"eda", "state":"thinking|done", "message":"profiling 12 columns" }
```

### 6.3 AG-UI events — backend → spectator dashboard (Person C owns exact wiring)
Event names track the AG-UI protocol: `RUN_STARTED`, `TEXT_MESSAGE_CONTENT`, `TOOL_CALL_START`, `TOOL_CALL_END`, `STATE_DELTA`, `HANDOFF`, `RUN_FINISHED`. Payload carries `{agent, tool, args, result, ts}`. Backend exposes an AG-UI-compatible endpoint (SSE or WS) that CopilotKit's `HttpAgent` registers against.

### 6.4 EDA result (internal, EDA agent → `panels`)
```json
{ "column":"price","dtype":"float64","missing_pct":3.2,"skew":2.1,"outlier_pct":5.0,
  "flags":["right_skewed","outliers"],"plot":"histogram","note":"log-transform candidate" }
```

### 6.5 Redis key schema
```
session:{sid}:dataset_profile      JSON  {schema, dtypes, n_rows, n_cols}
session:{sid}:eda_findings         JSON  [EDA result, ...]
session:{sid}:training:{run_id}    JSON  {latest_metrics, history_ref, status}
session:{sid}:memory               LIST  conversation turns (for callbacks/consistency)
session:{sid}:scratch:{agent}      JSON  per-agent working memory
```
Redis is the **single shared truth**; backend writes here and pushes deltas to VR + dashboard.

## 7. Sponsor integration map
| Sponsor | Prize | How we win it |
|---|---|---|
| **W&B Weave** | $1,000 | `@weave.op()` on every agent/tool call; route LLMs through **CoreWeave inference** (Nemotron 3 / Qwen3.5, `https://api.inference.wandb.ai/v1`); show Weave trace = the debrief, live. |
| **CopilotKit / AG-UI** | **AirPods Max ea.** | Spectator dashboard renders the live AG-UI event stream of the swarm. |
| **Redis** | Varmilo kb + 10k credits + hoodies | Shared agent memory + dataset/training state + conversation callbacks. |
| **OpenAI** | (judge favor) | Agents SDK handoffs/delegation per Kundel's gist; optional Realtime voice. |
| **Cursor** | (we build with it) | All three repos built via Cursor agents from these task files. |

> Confirm GLM availability in the W&B Playground before promising it; Nemotron 3 + Qwen3.5 are confirmed in the catalog.

## 8. Repo structure (monorepo)
```
hololab/
├── PLAN.md
├── backend/                 # Person A
│   ├── main.py              # FastAPI + WebSocket + AG-UI endpoint
│   ├── agents/{router,eda,training_monitor,narrator}.py
│   ├── tools/{profiling,plots,wandb_history,redis_state}.py
│   ├── contracts.py         # pydantic models for §6 schemas
│   └── requirements.txt
├── vr-client/               # Person B
│   ├── index.html
│   └── src/{scene,panels,scatter3d,loss_ribbon,voice,ws}.js
├── dashboard/               # Person C
│   └── (Next.js + CopilotKit app)
├── data/                    # pre-staged CSVs + a fake/replay run history JSON
└── scripts/{tunnel.sh, adb_reverse.sh, train_baseline.py}
```

## 9. Conventions
- Backend port **8080**; WS path **`/ws`**; AG-UI path **`/agui`**.
- Env: `OPENAI_API_KEY`, `WANDB_API_KEY`, `REDIS_URL`, `WANDB_INFERENCE_BASE=https://api.inference.wandb.ai/v1`.
- `contracts.py` is the law — if a schema changes, change it there and ping the channel.
- Each person works in their own folder; mock cross-boundary calls until integration sync points.

## 10. Demo script (3 min)
1. (10s) Put on Quest; you're standing in the data room, panels floating.
2. (40s) "Show me distributions and flag anything weird." → panels appear, skewed/outlier columns glow; agent narrates.
3. (30s) "Project this into 3D." → walk around the embedding scatter.
4. (50s) "Train a baseline and watch it." → loss ribbon streams; "is this overfitting?" → agent: "yes after step 90, here's why" (cites the curve).
5. (20s) Narrator speaks the eval report.
6. (10s) Pan the projector: AG-UI dashboard showed the whole swarm thinking + Weave trace link.
7. (20s) **Hand a judge the headset, let them speak a custom query.**

## 11. Timeline (Sat 11:15 → Sun 13:00). Sync points in **bold**.
| Block | A (backend) | B (VR) | C (glue/demo) |
|---|---|---|---|
| Sat 11–13 | scaffold FastAPI+WS, `contracts.py`, Redis up | WebXR scene boots in Quest via adb reverse | Next.js+CopilotKit skeleton, repo + tunnel |
| Sat 13–16 | EDA agent + profiling/plot tools | render `panels` from mock JSON | dashboard renders mock AG-UI events |
| **Sat 16: INTEGRATION 1** | real EDA over WS → B's panels; **end-to-end voice→panel works** |||
| Sat 16–19 | training-monitor + wandb history tool; 3D scatter payload | 3D scatter/embedding + loss ribbon | AG-UI endpoint wired to real swarm |
| Sat 19–21 | narrator + report; Weave on everything | voice in/out polished, controller interactions | demo datasets + replay run history staged |
| **Sun 9–10: INTEGRATION 2** | full hero loop on real data, dashboard mirroring |||
| Sun 10–12 | hardening, fallbacks | comfort/legibility polish | record backup video, rehearse script |
| Sun 12–13 | freeze, submit | freeze | submit + Devpost writeup |

## 12. Risks & fallbacks
- **Venue Wi‑Fi blocks WS** → cloudflared tunnel; if that fails, run backend on the laptop + `adb reverse` over USB tether.
- **Quest mic flaky** → controller text input fallback + pre-set example queries.
- **Live training unreliable** → replay W&B run history JSON (primary anyway).
- **CoreWeave inference latency/limits** → fall back to OpenAI models (still Weave-traced).
- **Integration slips** → every component must run standalone on mocks; the demo can degrade gracefully (drop scatter, keep panels + training).
