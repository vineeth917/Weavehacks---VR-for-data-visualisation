# Remote connect via ngrok (Person B & C)

Person A runs the backend at home. B and C connect over the public ngrok URL.

## Person A — start order

```bash
cd hacksweave
source .venv/bin/activate && set -a && source .env && set +a
MPLCONFIGDIR=/tmp/mpl uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

Second terminal:

```bash
./scripts/ngrok.sh
```

Copy the printed `https://….ngrok-free.app` URL into the group chat. **URL changes every ngrok restart** on the free plan.

---

## Person C — dashboard on localhost

Point your CopilotKit `HttpAgent` / SSE client at Person A's ngrok base.

| Endpoint | URL |
|----------|-----|
| AG-UI SSE | `https://<ngrok-host>/agui` |
| Button callbacks | `POST https://<ngrok-host>/agui/action` |
| Health | `https://<ngrok-host>/healthz` |

### ngrok browser warning (required)

Free ngrok shows an HTML interstitial unless the client sends:

```
ngrok-skip-browser-warning: 1
```

`curl` and server-side `fetch` work with that header. **Native browser `EventSource` cannot set custom headers.**

Pick one:

1. **Next.js rewrite proxy** (recommended) — browser hits same-origin `/api/agui`, your server proxies to ngrok with the header.
2. **`@microsoft/fetch-event-source`** (or similar) — pass the header on the SSE connection.
3. **CopilotKit runtime proxy** — if your HttpAgent supports a custom `fetch` / headers option, add the header there.

Example env (direct, only if your client supports the header):

```env
NEXT_PUBLIC_BACKEND_URL=https://xxxx.ngrok-free.app
NEXT_PUBLIC_AGUI_URL=https://xxxx.ngrok-free.app/agui
```

### Verify before wiring the UI

```bash
curl -H "ngrok-skip-browser-warning: 1" https://xxxx.ngrok-free.app/healthz
curl -N -H "ngrok-skip-browser-warning: 1" https://xxxx.ngrok-free.app/agui
# should see bytes within ~3s (replay buffer + pings)
```

### Button callbacks

```bash
curl -s https://xxxx.ngrok-free.app/agui/action \
  -H "Content-Type: application/json" \
  -H "ngrok-skip-browser-warning: 1" \
  -d '{"session_id":"default","userAction":{"name":"confirm_transform","surfaceId":"eda-action","context":{}}}'
```

A2UI shapes unchanged — see `BROADCAST_NOTE_A2UI.md`.

---

## Person B — Quest / WebXR

| Endpoint | URL |
|----------|-----|
| WebSocket | `wss://<ngrok-host>/ws` |
| Whisper STT | `POST https://<ngrok-host>/transcribe` |

Use the **wss://** URL (not ws://). Quest requires HTTPS/WSS for non-localhost.

Wire formats unchanged — see `CONTRACTS.md` and `BROADCAST_NOTE.md`.

If the Quest browser blocks the connection, confirm Person A's ngrok tunnel is up:

```bash
curl -H "ngrok-skip-browser-warning: 1" https://xxxx.ngrok-free.app/healthz
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| HTML page instead of JSON | Add `ngrok-skip-browser-warning: 1` |
| SSE connects, 0 bytes | Person A: restart uvicorn; re-run `./scripts/ngrok.sh` |
| `ERR_NGROK_3200` | Tunnel dead — Person A restarted ngrok; get new URL |
| Works for C, not Quest | Use `wss://` not `ws://`; check Quest URL has no typo |

Person A must keep the laptop awake (disable sleep) while B/C are connected.
