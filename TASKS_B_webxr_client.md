# TASKS_B — WebXR VR client (Meta Quest)

> Read `PLAN.md` first. You own the headset experience: the Three.js WebXR scene, floating stat panels, the 3D scatter/embedding, the loss ribbon, voice in/out, controller interactions, and the WebSocket client.
> **Feed `PLAN.md` + this file to your Cursor agent. Work in `vr-client/`.**

## Stack
Vanilla JS (no build step) · Three.js r159 via CDN · WebXR Device API · Web Speech API. Runs in the **Meta Quest browser**.

## Dev setup (do this first)
- USB-connect Quest, `adb reverse tcp:8080 tcp:8080`, open `http://localhost:8080` in the Quest browser → WebXR works (localhost = secure context, no TLS).
- Use the **Immersive Web Emulator** Chrome extension to iterate on desktop without the headset.
- Debug on-device via `chrome://inspect`.

## Dependencies / how to unblock yourself
- Build entirely against **mock JSON** matching §6.2 until A's server is live. Keep a `mocks/` folder with one of each message type.

## Task checklist
- [ ] **(0–2h)** Boot a WebXR scene: renderer with `xr.enabled`, `VRButton`, camera at eye height (0,1.6,3), grid floor, lights. Confirm it enters VR on the Quest.
- [ ] **(0–2h)** `ws.js`: connect to `ws://localhost:8080/ws`; dispatch incoming messages by `type`; expose `sendQuery(text)` / `sendCommand(action, params)`.
- [ ] **(2–5h)** `panels.js`: render a `panels` message as flat billboards (canvas-texture planes) arranged in an arc around the user. Decode `image_b64`. Apply a glow/border when `flags` present; react to `highlight` messages.
- [ ] **(by 6h INTEGRATION 1)** Real `voice_query` → A → render real `panels`. End-to-end.
- [ ] **(5–8h)** `scatter3d.js`: render a `scatter3d` message — points by x/y/z/color/size/shape, labeled axes, point label on point+dwell. User can walk around it.
- [ ] **(5–8h)** `loss_ribbon.js`: render streaming `training_update` messages as a ribbon/line you can walk along (train vs val, two colors); update in place each message.
- [ ] **(8–10h)** `voice.js`: push-to-talk on a controller button → `webkitSpeechRecognition` → `sendQuery`. **Request mic permission in the 2D browser before entering immersive.** TTS via `speechSynthesis` for `speech` and `report` messages.
- [ ] **(8–10h)** Controller interactions (primary): trigger = select panel/point (`interaction` message), thumbstick = rotate/scale scene, B-button = reset.
- [ ] **(10–12h)** Comfort/legibility polish: billboards face camera, points no closer than 0.8m, control panel within 30° of forward, viridis/tableau10 colors.
- [ ] **(stretch)** Hand tracking (pinch-rotate, point-tooltip) via WebXR Hand Input.

## ⚠️ Conflict-avoidance & coordination (read before coding)
- [ ] **Stay in your lane.** Own `vr-client/` (or your own repo) only. Never edit backend or dashboard code. If you need a backend change, ask A — don't patch it yourself.
- [ ] **Copy §6.2 field names verbatim** from `PLAN.md` into your mocks. Do **not** invent fields. If you need a field that isn't there, A must add it to `contracts.py` first.
- [ ] **Audio is yours alone.** The VR client speaks `speech`/`report` via TTS. The dashboard (C) only *displays* text — agree now that audio = VR only, so you don't double-narrate.
- [ ] **Transport must match A:** `ws://localhost:8080/ws`. Never hardcode a bare LAN IP over `http` — WebXR needs localhost or HTTPS.
- [ ] **Same `session_id`** convention as A on every client→server message, or Redis state won't line up.
- [ ] **Shared encodings:** viridis (sequential) / tableau10 (categorical), so VR and the dashboard look consistent.
- [ ] **Pin Three.js r159** (CDN) to match PLAN. Don't bump versions mid-hack.
- [ ] **Don't depend on the dashboard.** VR and dashboard both subscribe to A independently; either can be down without breaking the other.
- [ ] After each task: check the box, commit, push. Tell your Cursor agent the same lane rules.

## Definition of done
In the Quest: speak a query, panels appear and flag bad columns, walk around a 3D scatter, watch a loss ribbon stream, hear the report spoken — all driven by A's WS (and by mocks when A is down).

## Watch out
- WebXR needs HTTPS or localhost — never a bare LAN IP over http.
- Don't allocate geometry/materials per frame; build once, update transforms.
- Test text legibility in passthrough early; Quest lenses warm hues.
