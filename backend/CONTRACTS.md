# Backend interface contracts (for Person B & Person C)

> Source of truth: [`backend/contracts.py`](./contracts.py). This file mirrors the schemas for collaborators who shouldn't have to read Python. **Schema freeze rule:** if any field changes, the backend lead (Person A / vineeth) announces in the channel before merging.

**Current version: `0.0.2`** (interaction-loop + A2UI).
Server reports it at `GET /healthz → contracts_version`.

## Endpoints

| Path | Protocol | Who consumes |
|---|---|---|
| `GET /healthz` | HTTP | anyone — liveness + selected model names + contracts version + a2ui actions |
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

// pointer / panel selection / A2UI button click (action is now an open string)
{ "type": "interaction", "session_id": "s1",
  "action": "select_panel" | "select_point" | "grab_region"
          | "confirm_transform" | "dismiss"
          | "stop_training" | "keep_training",
  "target_id": "price_hist",          // optional
  "point_ids": ["r12", "r37", "r91"], // optional, for grab_region etc.
  "context": { ... }                   // optional, surfaces this through to handler
}

// A2UI userAction replay (alternative to interaction; same effect)
{ "type": "user_action", "session_id": "s1",
  "surface_id": "eda-action",
  "action": "confirm_transform",
  "context": { "column": "price", "transform": "log" } }
```

### Recognised `interaction.action` strings (frozen)

| action | typical payload | server reaction |
|---|---|---|
| `select_panel` | `target_id` | speech + highlight |
| `select_point` | `point_ids` or `target_id` | speech + highlight + emit `eda-action` surface |
| `grab_region` | `point_ids` | speech + highlight + emit `eda-action` surface |
| `confirm_transform` | `context={column, transform}` | apply transform, refresh `panels` |
| `dismiss` | `context={column}` | acknowledge, no state change |
| `stop_training` | `context={run_id, step, verdict}` | emit `training_update` status=`stopped` |
| `keep_training` | `context={run_id, step, verdict}` | acknowledge |

Unknown actions return `agent_status` with `state="error"` — clients must tolerate.

## 2. Backend → Client (over `/ws`)

```jsonc
{ "type": "speech",  "agent": "eda",
  "text": "Three columns are right-skewed: price, income, balance." }

{ "type": "panels",
  "panels": [ { "id": "price_hist", "kind": "histogram", "title": "Price",
                "column": "price", "image_b64": "<png>",
                "position_hint": "left", "flags": ["right_skewed", "outliers"] } ] }

{ "type": "scatter3d", "title": "Embedding projection (PC1 32%, PC2 18%, PC3 11%)",
  "axes": { "x": "PC1", "y": "PC2", "z": "PC3" },
  "points": [ { "id": "r0", "x": 0.1, "y": -0.4, "z": 0.7,
                "color": "#3cb371", "size": 0.03, "shape": "sphere",
                "label": "0" } ] }

// NEW in 0.0.2: 2D density surface (KDE) for the VR client
{ "type": "surface", "title": "KDE(price, income)",
  "axes": { "x": "price", "y": "income", "z": "density" },
  "grid": 48,
  "x_extent": [0.0, 1000.0], "y_extent": [0.0, 500000.0],
  "z": [[0.001, 0.004, ...], ...]   // shape grid×grid, normalised [0,1]
}

// NEW in 0.0.2: 2D field (e.g. correlation matrix)
{ "type": "field", "title": "Correlation field",
  "labels": ["price","income","balance","age"],
  "values": [[1.0,0.41,-0.02,0.03], ...],
  "range": [-1.0, 1.0] }

{ "type": "training_update", "run_id": "r1", "step": 120,
  "metrics": { "train_loss": 0.21, "val_loss": 0.33 },
  "status": "running" | "stopped" | "done" }

{ "type": "highlight", "target_ids": ["price_hist"], "reason": "skew" }

{ "type": "report", "speak": true,
  "verdict": "Model is mildly overfitting after step 90.",
  "sections": [ { "title": "Metrics", "body": "val_loss diverges..." } ] }

{ "type": "agent_status", "agent": "eda",
  "state": "thinking" | "tool_call" | "handoff" | "done" | "error",
  "message": "profiling 12 columns" }
```

## 3. AG-UI events (over `/agui` SSE)

CopilotKit-compatible. The new `event` values added in 0.0.2 are `CUSTOM` and `USER_ACTION`. SSE format:

```
event: CUSTOM
data: {"event":"CUSTOM","name":"surfaceUpdate","value":{...A2UI envelope...},"ts":1.78e9}
```

Event vocabulary:

| event | when | payload |
|---|---|---|
| `RUN_STARTED` / `RUN_FINISHED` | agent turn boundaries | `{agent, args}` |
| `TEXT_MESSAGE_CONTENT` | agent emits chat text | `{agent, args:{text}}` |
| `TOOL_CALL_START` / `TOOL_CALL_END` | a tool ran | `{agent, tool, args, result}` |
| `STATE_DELTA` | misc server state pings (incl. heartbeat) | `{args}` |
| `HANDOFF` | router → specialist agent | `{agent, args:{from,to,reason}}` |
| **`CUSTOM`** *(new)* | wrap A2UI envelopes | `{name, value, agent}` |
| **`USER_ACTION`** *(new)* | client replayed an A2UI userAction back | `{value:{action, context, target_id}}` |

## 4. A2UI surfaces (rendered by dashboard via CopilotKit)

The backend emits **three** surfaces (v0.8 envelopes; CopilotKit accepts both v0.8 & v0.9):

| `surfaceId` | trigger | components | buttons → actions |
|---|---|---|---|
| `eda-findings` | end of an EDA agent turn | Column + List of `{column, flag, note}` | — display only |
| `eda-action` | EDA suggests a transform | Card with prompt + Confirm/Dismiss | `confirm_transform`, `dismiss` |
| `training-verdict` | training-monitor verdict | Card with verdict + Stop/Keep | `stop_training`, `keep_training` |

Envelope order per surface (v0.8): `surfaceUpdate` → `dataModelUpdate` → `beginRendering`. See [`a2ui/SPEC_NOTES.md`](./a2ui/SPEC_NOTES.md) for the JSON shapes and the v0.8/v0.9 mapping.

## Mock messages

See [`mocks/`](./mocks/) for sample payloads you can replay against your client.

---

## Changelog

### 0.0.2 — interaction-loop + A2UI  *(needs sign-off from Person C on the A2UI surface IDs / action strings)*

- **Breaking:** `Interaction.action` is now an open string (was `Literal["select_panel","select_point"]`). New recognised values listed above.
- **Additive:** `Interaction.target_id` and `Interaction.point_ids` are both optional; `Interaction.context` (free-form dict) added.
- **Additive:** new client message type `user_action` (A2UI userAction replay over /ws).
- **Additive:** new outbound types `surface` (KDE) and `field` (correlation matrix), used by the new `project_3d` / `kde_surface` / `corr_field` tools.
- **Additive:** AG-UI `event` enum gained `CUSTOM` and `USER_ACTION`. `AGUIEvent` gained `name` and `value` fields (both optional).
- **New:** three A2UI surfaces (`eda-findings`, `eda-action`, `training-verdict`) emitted on `/agui`.

### 0.0.1 — initial scaffold
- WS messages per PLAN §6.1 / §6.2; AG-UI heartbeat stub.
