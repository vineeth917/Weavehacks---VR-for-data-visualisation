# TASKS_C — Glue, AG-UI spectator dashboard & demo (AirPods owner)

> Read `PLAN.md` first. You own the **CopilotKit/AG-UI spectator dashboard** (the AirPods Max prize), the deploy/streaming pipeline, the staged demo data, and the run-of-show. You make the swarm *visible* to judges.
> **Feed `PLAN.md` + this file to your Cursor agent. Work in `dashboard/` + `scripts/` + `data/`.**

## Stack
Next.js + React · CopilotKit + `@ag-ui/client` · cloudflared · pandas (data staging).

## Dependencies / how to unblock yourself
- Build the dashboard against **mock AG-UI events** (§6.3) until A's `/agui` endpoint is live. Keep a `mocks/agui-stream.json`.

## Task checklist
- [ ] **(0–2h)** Repo hygiene: monorepo layout per `PLAN.md §8`, shared `.env.example`, README with run commands. Scaffold Next.js + CopilotKit app.
- [ ] **(0–2h)** `scripts/adb_reverse.sh` and `scripts/tunnel.sh` (`cloudflared tunnel --url http://localhost:8080`). Document the demo network path.
- [ ] **(2–5h)** Dashboard: register A's backend as a CopilotKit `HttpAgent` pointing at `/agui`. Render the live event stream — a timeline/graph of agents, current tool calls, handoffs, and state deltas. This is the "swarm thinking" view for the projector.
- [ ] **(2–4h)** `data/`: stage **3 demo datasets** (e.g. a clean tabular set, one with obvious skew/outliers/missingness, one ML-ish). Pre-extract schemas.
- [ ] **(4–6h)** `data/replay_run_history.json`: a realistic train/val loss history that **clearly shows overfitting after a point** (so the training-monitor has something to catch). Coordinate the format with A's `get_run_history`.
- [ ] **(by 6h INTEGRATION 1)** Dashboard rendering real AG-UI events from A (even if only EDA so far).
- [ ] **(6–9h)** Add a Weave trace link/badge to the dashboard so judges can click through to the trace = the debrief.
- [ ] **(9–11h)** Wire the full pipeline: confirm VR (B) + dashboard (C) both reflect the same swarm run simultaneously.
- [ ] **(11–12h)** Record a **backup demo video** (in case live fails), write the Devpost/README submission, rehearse the §10 script and time it to 3 min.

## ⚠️ Conflict-avoidance & coordination (read before coding)
- [ ] **Stay in your lane.** Own `dashboard/`, `scripts/`, `data/` (or your own repo) only. Never edit backend agent logic or the VR scene. If the backend needs a change for deploy, file it to A — don't patch backend code.
- [ ] **Agree the AG-UI transport with A in hour one** — SSE vs WS, and the exact event names/payloads (§6.3). Don't guess; build mocks from whatever A confirms.
- [ ] **`replay_run_history.json` is an A↔C contract.** You author the file, but A consumes it — agree the exact schema with A *before* building it, or the training-monitor breaks.
- [ ] **Display, don't speak.** The dashboard shows the `report` as text; VR speaks it. No audio in the dashboard.
- [ ] **Dashboard must not depend on the VR client.** Both subscribe to A independently.
- [ ] **You own `.env.example` + ports** per PLAN §9. Any new env var is coordinated with A first (names must match the backend).
- [ ] **Network bring-up is yours to rehearse:** run `adb reverse` (B's machine) + `cloudflared` once *with* A and B before judging. Wi‑Fi is the #1 demo killer.
- [ ] After each task: check the box, commit, push. Tell your Cursor agent the same lane rules.

## Definition of done
On the projector, judges watch the agent swarm reason in real time (messages, tool calls, handoffs, Weave link) while the human in the headset drives it. Deploy path works on venue Wi‑Fi with a tested fallback. Submission + backup video ready before Sun 12:00.

## Watch out
- AG-UI is event-based (SSE/WS) — confirm transport with A early so event names/payloads match §6.3.
- Don't let the dashboard depend on the VR client; both subscribe to A independently.
- Rehearse the network bring-up (adb reverse → tunnel) at least once before judging; Wi‑Fi is the #1 demo killer.
