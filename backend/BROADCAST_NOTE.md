# 📣 Contracts bump 0.0.1 → 0.0.2 — interaction-loop + A2UI

> **Action required:** Person C must sign off on the A2UI surface IDs and the action strings (see "C" section below). Person B has no breaking changes but should glance at the action enum.

## What changed

1. `Interaction.action` is now an **open string** instead of a 2-value enum. Server still validates the body shape; unknown actions get a structured `agent_status` error rather than a parse failure.
2. New recognised action strings (frozen — exact match):
   - `select_point`, `grab_region` (VR)
   - `confirm_transform`, `dismiss` (A2UI eda-action card)
   - `stop_training`, `keep_training` (A2UI training-verdict card)
3. New outbound message types: `surface` (KDE density grid) and `field` (correlation matrix). Both ride on the existing `/ws`.
4. New AG-UI event types: `CUSTOM` (wraps A2UI v0.8 envelopes) and `USER_ACTION` (replay of an A2UI button click).
5. Backend now emits 3 A2UI surfaces on `/agui`: `eda-findings`, `eda-action`, `training-verdict`.
6. Bug fix: `near_constant` flag now uses dominant-value ≥ 95 % (was `unique/n_rows ≤ 1 %` which falsely flagged 4-class categoricals).

## For Person B (VR client) — NON-breaking

- Your existing `{"type":"interaction","action":"select_panel","target_id":...}` still works exactly as before.
- New optional fields on `Interaction`: `point_ids: string[]` and `context: object`. Use them when you have a multi-point selection.
- When you fire a recognised action, expect this response sequence:
  ```
  agent_status thinking → speech → highlight → agent_status done
  ```
- You may also receive new payload types `surface` (KDE) and `field` (corr matrix). If you can't render them yet, drop them — schema is finalised.
- The training-monitor will, in a later phase, accept `stop_training` / `keep_training` from you the same way the dashboard does — synthesize them via `interaction` with the matching action string.

## For Person C (dashboard) — needs sign-off

- Backend emits A2UI envelopes as AG-UI `CUSTOM` events on `/agui`, in this order per surface:
  ```
  event: CUSTOM
  data: {"event":"CUSTOM","name":"surfaceUpdate","value":{"surfaceUpdate":{...}},"ts":...}
  event: CUSTOM
  data: {"event":"CUSTOM","name":"dataModelUpdate","value":{"dataModelUpdate":{...}},"ts":...}
  event: CUSTOM
  data: {"event":"CUSTOM","name":"beginRendering","value":{"beginRendering":{...}},"ts":...}
  ```
- **Ships v0.8 envelope names** (the strings the user asked for); CopilotKit's A2UI middleware accepts both v0.8 and v0.9. If your renderer is v0.9-only, ping me and I'll flip `backend/a2ui/__init__.py:SPEC_VERSION = "0.9"` — emitter swaps to `createSurface` / `updateComponents` / `updateDataModel` automatically.
- **Please confirm:**
  - [ ] surface IDs OK: `eda-findings`, `eda-action`, `training-verdict`
  - [ ] action strings OK: `confirm_transform`, `dismiss`, `stop_training`, `keep_training`
  - [ ] CUSTOM event wrapper OK: `name = <envelope key>`, `value = <full envelope>` (alternative would be `name = "a2ui"` with envelope nested under `value.envelope`)
  - [ ] component types in use: `Column`, `Row`, `Card`, `Text`, `Button`, `List` — confirm they're all in your catalog
  - [ ] when the user clicks a button, you POST back to me on `/ws` as `user_action` (or `interaction` with the same action string); see CONTRACTS.md §1

## Test it locally

```bash
# 1. start backend
source .venv/bin/activate
uvicorn backend.main:app --port 8080

# 2. in another shell: stream /agui
curl -N http://localhost:8080/agui

# 3. in a third shell: trigger the EDA action surface
python backend/scripts/test_phase_a2ui.py
```

The third command sends a `select_point` over `/ws`, and you'll see the 3 A2UI envelopes (`surfaceUpdate`, `dataModelUpdate`, `beginRendering`) flow on `/agui` in order.

## See also

- [`CONTRACTS.md`](./CONTRACTS.md) — full schema reference
- [`a2ui/SPEC_NOTES.md`](./a2ui/SPEC_NOTES.md) — A2UI v0.8 vs v0.9 details + JSON shapes
- [`a2ui/surfaces.py`](./a2ui/surfaces.py) — the three surface factories
