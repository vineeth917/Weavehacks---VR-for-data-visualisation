# Backend interface contracts (for Person B & Person C)

> Source of truth: [`backend/contracts.py`](./contracts.py). This file mirrors the schemas for collaborators who shouldn't have to read Python. **Schema freeze rule:** if any field changes, the backend lead (Person A / vineeth) announces in the channel before merging.

Endpoints:

| Path | Protocol | Who consumes |
|---|---|---|
| `GET /healthz` | HTTP | anyone — liveness + selected model names |
| `WS /ws` | WebSocket | Person B's WebXR client |
| `GET /agui` | Server-Sent Events | Person C's CopilotKit dashboard |

Default port: **8080**.

---

## 1. Client → Backend (over `/ws`)

All messages JSON, must include `type` and `session_id`.

```jsonc
// voice or controller text input
{ "type": "voice_query", "session_id": "s1", "text": "which columns are skewed?" }

// app-level commands
{ "type": "command", "session_id": "s1",
  "action": "load_dataset" | "train_baseline" | "run_evals" | "reset",
  "params": {} }

// pointer / panel selection in VR
{ "type": "interaction", "session_id": "s1",
  "action": "select_panel" | "select_point", "target_id": "price_hist" }
```

## 2. Backend → Client (over `/ws`)

Any of the following may arrive at any time; clients should switch on `type`.

```jsonc
{ "type": "speech",  "agent": "eda",
  "text": "Three columns are right-skewed: price, income, balance." }

{ "type": "panels",
  "panels": [
    { "id": "price_hist", "kind": "histogram", "title": "Price",
      "column": "price", "image_b64": "<png>",
      "position_hint": "left", "flags": ["right_skewed", "outliers"] }
  ] }

{ "type": "scatter3d", "title": "Embedding projection",
  "axes": { "x": "PC1", "y": "PC2", "z": "PC3" },
  "points": [ { "id": "r0", "x": 0.1, "y": -0.4, "z": 0.7,
                "color": "#3cb371", "size": 0.04, "shape": "sphere",
                "label": "row 0" } ] }

{ "type": "training_update", "run_id": "r1", "step": 120,
  "metrics": { "train_loss": 0.21, "val_loss": 0.33 },
  "status": "running" }

{ "type": "highlight", "target_ids": ["price_hist"], "reason": "skew" }

{ "type": "report", "speak": true,
  "verdict": "Model is mildly overfitting after step 90.",
  "sections": [ { "title": "Metrics",
                  "body": "val_loss diverges from train_loss..." } ] }

{ "type": "agent_status", "agent": "eda",
  "state": "thinking" | "tool_call" | "handoff" | "done" | "error",
  "message": "profiling 12 columns" }
```

## 3. AG-UI events (over `/agui` SSE)

CopilotKit-compatible. One event per SSE message; `data` is JSON:

```jsonc
{
  "event": "RUN_STARTED" | "TEXT_MESSAGE_CONTENT" | "TOOL_CALL_START"
         | "TOOL_CALL_END"  | "STATE_DELTA" | "HANDOFF" | "RUN_FINISHED",
  "agent": "eda" | "training_monitor" | "narrator" | "router" | "system" | null,
  "tool":  "profile_dataset" | "render_plot" | ... | null,
  "args":  { ... } | null,
  "result": <any> | null,
  "ts": 1733520000.123
}
```

In Phase 0 the endpoint emits a `STATE_DELTA` heartbeat every 5s so you can wire your `EventSource` early.

## Mock messages

See [`mocks/`](./mocks/) for sample payloads you can replay against your client.
