# A2UI v0.8 envelopes — implementation notes (HoloLab backend)

> Sources read: <https://a2ui.org/specification/v0.8-a2ui/>, <https://a2ui.org/specification/v0.9-a2ui/>, <https://a2ui.org/specification/v0.9-evolution-guide/>, <https://docs.copilotkit.ai/built-in-agent/generative-ui/a2ui>, the Google `google/A2UI` repo migration table.

---

## ⚠ Version naming — read first

The user's spec text says **"A2UI v0.9"** but lists the **v0.8** envelope names:

| user-named (this repo) | actual v0.8 | actual v0.9 (current) |
|---|---|---|
| `surfaceUpdate` | ✅ `surfaceUpdate` | renamed → `updateComponents` |
| `dataModelUpdate` | ✅ `dataModelUpdate` | renamed → `updateDataModel` |
| `beginRendering` | ✅ `beginRendering` | renamed → `createSurface` (and is now the FIRST message, not last) |

We **ship the v0.8 envelope names** the user explicitly wrote, because:

1. The user listed those literal strings — silently switching to v0.9 names would break their spec.
2. CopilotKit's `A2UIMiddleware` (the docs page the user pointed at) is built to translate between v0.8 and v0.9 schemas transparently, so the dashboard renders either.
3. A flag in `backend/a2ui/__init__.py` (`SPEC_VERSION = "0.8"`) lets us upgrade with a one-line change once Person C confirms the renderer version. v0.9 is added behind the flag in a follow-up.

If Person C says "renderer is v0.9-only", flip `SPEC_VERSION = "0.9"` and the emitter will substitute the new envelope keys + payload shape.

---

## Envelope shapes (v0.8, JSONL, server→client)

Each message is a single JSON object with **exactly one** of: `surfaceUpdate`, `dataModelUpdate`, `beginRendering`, `deleteSurface`. Send them on the wire one-per-line.

### 1. `surfaceUpdate` — declare/update components

```jsonc
{
  "surfaceUpdate": {
    "surfaceId": "eda-findings",
    "components": [
      { "id": "root",
        "component": { "Column": { "children": { "explicitList": ["title", "findings_list"] } } } },
      { "id": "title",
        "component": { "Text": { "usageHint": "h3",
                                 "text": { "literalString": "EDA findings" } } } },
      { "id": "findings_list",
        "component": { "List": { "dataBinding": "/findings",
                                 "componentId": "finding_row" } } },
      { "id": "finding_row",
        "component": { "Row": { "alignment": "spaceBetween",
                                "children": { "explicitList": ["col_name", "col_flag"] } } } },
      { "id": "col_name", "component": { "Text": { "text": { "path": "/name" } } } },
      { "id": "col_flag", "component": { "Text": { "text": { "path": "/flag" } } } }
    ]
  }
}
```

- One component MUST have `id: "root"` — the renderer uses it as the tree root.
- Properties are either `{"literalString": "..."}` for static values, or `{"path": "/json/pointer"}` for data-bound values, or both (`path` with `literalString` fallback).
- Children of containers go inside `{"children": {"explicitList": [...]}}`. Lists iterate via `{"dataBinding": "/path", "componentId": "rowTemplate"}`.
- Components can arrive in **any order**; the client buffers until `beginRendering`.

### 2. `dataModelUpdate` — supply / replace surface state

```jsonc
{
  "dataModelUpdate": {
    "surfaceId": "eda-findings",
    "contents": [
      { "key": "findings",
        "valueList": [
          { "valueMap": [ { "key": "name", "valueString": "price" },
                           { "key": "flag", "valueString": "right_skewed" } ] },
          { "valueMap": [ { "key": "name", "valueString": "balance" },
                           { "key": "flag", "valueString": "left_skewed" } ] }
        ] }
    ]
  }
}
```

v0.8 uses an **adjacency-list** with typed values: `valueString`, `valueNumber`, `valueBoolean`, `valueList`, `valueMap`. Each entry has exactly one `value*` key.

(v0.9 collapsed this to plain JSON: `{"contents": {"findings": [{"name":"price","flag":"right_skewed"}]}}`. CopilotKit accepts both; we ship v0.8.)

### 3. `beginRendering` — render signal

```jsonc
{
  "beginRendering": {
    "surfaceId": "eda-findings",
    "root": "root",
    "catalogId": "basic"
  }
}
```

The client buffers all prior `surfaceUpdate` / `dataModelUpdate` messages keyed by `surfaceId`, then renders when `beginRendering` arrives for that surface. **Order matters per surface, but messages for different surfaces can interleave.**

### Action round-trip (client→server `userAction`)

When the user taps a button defined in a surface, the renderer posts back to the agent:

```jsonc
{
  "userAction": {
    "surfaceId": "eda-action",
    "action": "confirm_transform",
    "context": { "column": "price", "transform": "log" }
  }
}
```

- The `action` string is whatever we wrote in the button's `action` field of `surfaceUpdate`.
- The `context` is whatever fields the button declared in its `context` map.
- We mount these on the existing `/ws` channel with a thin adapter (see §5 below) so we don't need a second back-channel for the demo.

---

## AG-UI transport — wrapping A2UI in CUSTOM events

AG-UI's `CUSTOM` event is the generic extension hook. Per CopilotKit's A2UIMiddleware, A2UI envelopes ride inside `CUSTOM` events with the envelope key as the event `name`. We emit one AG-UI event per A2UI envelope, in order:

```jsonc
// SSE frame (one per line of JSONL); `data` is a JSON-stringified AG-UI event
event: CUSTOM
data: {"type":"CUSTOM","name":"surfaceUpdate","value":{"surfaceUpdate":{...}},"ts":1.78e9}

event: CUSTOM
data: {"type":"CUSTOM","name":"dataModelUpdate","value":{"dataModelUpdate":{...}},"ts":1.78e9}

event: CUSTOM
data: {"type":"CUSTOM","name":"beginRendering","value":{"beginRendering":{...}},"ts":1.78e9}
```

We bundle this triple as the helper `emit_surface(surface_id, components, data)` in `backend/a2ui/emitter.py`. The dashboard's `EventSource` listener can either:

1. Treat each CUSTOM event individually (CopilotKit A2UIMiddleware path), or
2. Group by `surfaceId` and apply the v0.8 buffering rules.

Either works because the order on the wire is `surfaceUpdate → dataModelUpdate → beginRendering`.

---

## What this repo ships (Phase: interaction-loop + A2UI)

| Surface ID | Trigger | Components | Actions |
|---|---|---|---|
| `eda-findings` | EDA agent finishes profiling | List of `{column, flag, note}` rows | none — display only |
| `eda-action` | EDA agent suggests a transform | Card with prompt text + Confirm/Dismiss buttons | `confirm_transform`, `dismiss` |
| `training-verdict` | Training-monitor returns a verdict | Card with verdict text + Stop/Keep buttons | `stop_training`, `keep_training` |

Action strings are **frozen** — exact matches required.

Surfaces are authored as Python dicts in `backend/a2ui/surfaces.py` (one factory function per surface) and **also pasted into the EDA / training-monitor system prompts as templates** so the agent can fill in field values inline when it has enough info to skip the in-code factory.

---

## Pointers for Person C (dashboard, sign-off needed)

- The 3 surfaces above are the entire A2UI surface vocabulary the backend will emit in the demo. If your renderer needs different IDs or button strings, ping me **before** Phase 4 — those strings are baked into agent prompts.
- We emit v0.8 envelopes inside AG-UI CUSTOM events with `name == envelope key`. If CopilotKit's middleware expects a different wrapper (e.g. `name: "a2ui"` with the envelope nested under `value.envelope`), tell me and I'll flip the emitter.
- Buffer rule: only render a surface after its `beginRendering`.

## Pointers for Person B (VR client)

- A2UI never reaches the VR client — it goes to the dashboard on `/agui`. Your `/ws` traffic is unchanged except that `interaction.action` is now an open string (was a 2-value enum). The new actions you can fire: `select_point`, `grab_region`, `confirm_transform`, `dismiss`, `stop_training`, `keep_training`. The handlers tolerate unknown strings.
