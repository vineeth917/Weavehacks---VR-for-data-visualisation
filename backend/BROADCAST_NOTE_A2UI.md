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

### What backend ACCEPTS as a callback

When the user clicks an A2UI button, the renderer dispatches a
client→server payload. Backend handles **four** shapes interchangeably,
all routed through `_handle_user_action()` — single source of truth, so
emit-the-same-shape-everywhere is guaranteed.

#### A. `POST /agui/action`  ← **CANONICAL FOR PERSON C**

This is the path Person C's `@copilotkit/a2ui-renderer` dispatches to.
Body shape (v0.8 `userAction` envelope):

```json
POST /agui/action
Content-Type: application/json
{
  "session_id": "<sid>",
  "userAction": {
    "name":      "confirm_transform",
    "surfaceId": "eda-action",
    "context":   { "column": "fare", "transform": "log" }
  }
}
```

Response: `200 {"ok": true, "interaction": {...}}`. Backend also
immediately fans out a `USER_ACTION` event on `/agui` with
`args.via = "POST /agui/action"` so the dashboard / dev UI sees the
round-trip without polling.

This endpoint is **what to wire your renderer at**. Smoke-tested green
end-to-end:

```
$ curl -s http://localhost:8080/agui/action -d '{
    "session_id":"demo",
    "userAction":{"name":"confirm_transform","surfaceId":"eda-action",
                  "context":{"column":"fare","transform":"log"}}}'
{"ok":true,"interaction":{...}}
```

#### B. `POST /action`

Compatibility alias for /agui/action — accepts the exact same body and
also accepts the v0.9 spec wrap `{"action": {"name": ..., ...}}`.
Behaves identically.

#### C. WebSocket `/ws` — `{"type":"user_action", ...}`

V0.8 mirror, for clients that already hold a WebSocket open:

```json
{
  "type":       "user_action",
  "session_id": "...",
  "surface_id": "eda-action",
  "action":     "confirm_transform",
  "context":    { "column": "fare", "transform": "log" }
}
```

#### D. WebSocket `/ws` — `{"type":"interaction", ...}`

Identical payload shape to (C) but `type: "interaction"` — what the VR
client uses. All four shapes converge to the same emit code path.

### What we need from Person C (live test)

1. Point your renderer's action POST URL at `http://<backend>/agui/action`
   (NOT `/action`; both work, but `/agui/action` is the documented one).
2. Run with `BUTTON_MODE=v08` first (default — no env var needed). Click
   any A2UI button.
3. Confirm the dashboard sees a `USER_ACTION` event on `/agui` with
   `args.action` set to one of the four frozen names
   (`confirm_transform`, `dismiss`, `stop_training`, `keep_training`).
4. If clicks do NOT round-trip, capture the HTTP body your renderer
   posted (browser devtools network tab) and paste it into the shared
   channel — we'll add a fifth compatibility shape rather than asking
   you to change your renderer.

If you also want to test the v0.9 emit shape (action.event.name in the
button component), restart backend with `A2UI_BUTTON_MODE=v09` and
re-render. The callback path (POST /agui/action) is the same in both
modes.

**Do not mark this resolved on mocks.** It must be tested with the real
`@copilotkit/a2ui-renderer v1.59.5` against a running `uvicorn`.
