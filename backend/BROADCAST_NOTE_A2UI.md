# Broadcast — A2UI surface contracts + button wiring (Person A → Person C)

> **STATUS: FROZEN for items 1–3 below. Item 4 (button onPress shape)
> requires a live test with Person C's `@copilotkit/a2ui-renderer v1.59.5`
> renderer before we mark it resolved.**

We finished `TASKS_A` (narrator phase). Before we hand off for joint
integration, here is the final state of every contract surface between the
HoloLab backend and the dashboard renderer.

---

## 1. `eda-action` data shape — CONFIRMED

C's renderer reads `/prompt` and `/rationale`. Backend emits exactly that:

```json
// dataModelUpdate.contents (under contents/values when adjacency-listed)
{
  "prompt":    "Log-transform `fare`?",
  "rationale": "`fare` is right-skewed — a log transform usually helps downstream models converge."
}
```

Source: `backend/a2ui/surfaces.py` → `eda_action()`. Components reference
`/prompt` (heading) and `/rationale` (body). **No change needed.**

---

## 2. `eda-findings` row shape — CONFIRMED

C's renderer reads `/column`, `/flag`, `/note` (singular `flag`). Backend
emits exactly that:

```json
{
  "findings": [
    { "column": "fare", "flag": "right_skewed", "note": "consider log-transform" },
    { "column": "cabin", "flag": "heavy_missing", "note": "77% missing — drop or impute" }
  ]
}
```

Source: `backend/main.py::_emit_findings_surface` builds each row with
exactly `{column, flag, note}` (singular `flag`, never plural). The agent
respects a flag-priority rule
`heavy_missing > near_constant > outliers > skewed > high_cardinality`, so
the single `flag` field is always the most-actionable label for that
column. **No change needed.**

---

## 3. `replay_run_history.json` schema — FROZEN

See `backend/BROADCAST_NOTE_TRAINING.md`. Keyed `{run_id: {config,
metrics, summary}}` format. C is updating `LossChart` to match. Will not
change.

---

## 4. A2UI Button action wire shape — TWO MODES, FLAG-GATED

**This is the one that needs a live test.** Background:

- Person C: *"v0.9 may wire button callbacks through `onPress.name` but we
  emit a flat `action` field."*
- A2UI v0.9 spec (`https://a2ui.org/concepts/actions/`): the canonical
  shape is `action: {event: {name, context}}` — NOT `onPress` in the spec
  itself. C may be using a renderer wrapper that renames it to `onPress`
  internally; the wire format we control is the v0.9 spec one.

### What backend emits (both modes implemented behind `A2UI_BUTTON_MODE`)

**Default — `A2UI_BUTTON_MODE=v08` (our current tested shape):**

```json
{
  "id": "confirm",
  "component": {
    "Button": {
      "label":   { "literalString": "Apply transform" },
      "action":  "confirm_transform",
      "variant": "primary",
      "context": {
        "contents": [
          { "key": "column",    "valueString": "fare" },
          { "key": "transform", "valueString": "log" }
        ]
      }
    }
  }
}
```

**Opt-in — `A2UI_BUTTON_MODE=v09` (canonical A2UI v0.9 Action schema):**

```json
{
  "id": "confirm",
  "component": {
    "Button": {
      "label":   { "literalString": "Apply transform" },
      "variant": "primary",
      "action": {
        "event": {
          "name":    "confirm_transform",
          "context": { "column": "fare", "transform": "log" }
        }
      }
    }
  }
}
```

To flip the mode without redeploying: set `A2UI_BUTTON_MODE=v09` in the
backend env and restart `uvicorn`. The action *string names* (the four
frozen `ACTIONS`: `confirm_transform`, `dismiss`, `stop_training`,
`keep_training`) are unchanged in both modes.

### What backend ACCEPTS as a callback (both shapes work today)

When the user clicks an A2UI button, the renderer dispatches a
client→server payload. Backend handles **three** shapes interchangeably:

1. **v0.8 mirror over WebSocket** (`/ws`) — the `UserAction` contract:
   ```json
   {
     "type": "user_action",
     "session_id": "...",
     "surface_id": "eda-action",
     "action":     "confirm_transform",
     "context":    { "column": "fare", "transform": "log" }
   }
   ```

2. **Native `interaction` over WebSocket** (`/ws`) — what the VR client
   already uses, identical payload shape but `type: "interaction"`.

3. **v0.9 HTTP POST** (`/action`, new in this phase) — for HTTP-only
   dashboards or A2UI renderers that prefer not to hold a WebSocket open
   for callbacks. Accepts the v0.9 canonical payload directly:
   ```json
   POST /action
   {
     "session_id": "...",
     "action": {
       "name":              "confirm_transform",
       "surfaceId":         "eda-action",
       "sourceComponentId": "confirm",
       "timestamp":         "2026-06-06T17:00:00Z",
       "context":           { "column": "fare", "transform": "log" }
     }
   }
   ```
   Backend normalises this to an internal `Interaction` and emits a
   `USER_ACTION` on `/agui` so the round-trip is visible in traces.

### What we need from Person C (live test)

Spin the renderer up against a backend running with `BUTTON_MODE=v08`
first. If your button clicks reach `/agui` as `USER_ACTION` events with
the four frozen action names, **we're done** — keep v08.

If clicks DO NOT reach `/agui`, flip to `BUTTON_MODE=v09`, restart, and
retry. If they still don't, capture the HTTP/WS payload your renderer
*does* emit (browser devtools network tab) and paste it into the shared
channel — we'll add a third compatibility shape rather than asking you to
change your renderer.

**Do not mark this resolved on mocks.** It must be tested with the real
`@copilotkit/a2ui-renderer v1.59.5` against a running `uvicorn`.
