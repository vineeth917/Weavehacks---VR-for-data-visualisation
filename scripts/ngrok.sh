#!/usr/bin/env bash
# Expose local HoloLab backend (default :8080) via ngrok HTTPS/WSS.
# Use this when B/C are on their own Wi-Fi (remote from Person A).
#
# Prereqs (Person A, once):
#   brew install ngrok
#   ngrok config add-authtoken <token>   # https://dashboard.ngrok.com/get-started/your-authtoken
#
# Usage:
#   ./scripts/ngrok.sh              # tunnel → localhost:8080
#   PORT=9000 ./scripts/ngrok.sh
#
# After start, copy the printed https://*.ngrok-free.app URLs into the group chat.
# URL changes every restart on the free plan.
#
set -euo pipefail

PORT="${PORT:-8080}"
LOCAL="http://127.0.0.1:${PORT}"

if ! curl -sf "${LOCAL}/healthz" >/dev/null 2>&1; then
  echo "error: backend not reachable at ${LOCAL}/healthz — start uvicorn first:" >&2
  echo "  source .venv/bin/activate && set -a && source .env && set +a" >&2
  echo "  MPLCONFIGDIR=/tmp/mpl uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}" >&2
  exit 1
fi

if ! command -v ngrok >/dev/null 2>&1; then
  echo "error: ngrok not found — brew install ngrok" >&2
  exit 1
fi

if ! ngrok config check >/dev/null 2>&1; then
  echo "error: ngrok not configured — ngrok config add-authtoken <token>" >&2
  exit 1
fi

cleanup() {
  if [[ -n "${NGROK_PID:-}" ]] && kill -0 "${NGROK_PID}" 2>/dev/null; then
    kill "${NGROK_PID}" 2>/dev/null || true
    wait "${NGROK_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "starting ngrok → ${LOCAL} (Ctrl-C to stop)" >&2
ngrok http "${PORT}" --log=stderr &
NGROK_PID=$!

PUBLIC=""
for _ in $(seq 1 40); do
  PUBLIC="$(curl -sf http://127.0.0.1:4040/api/tunnels 2>/dev/null \
    | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for t in data.get('tunnels', []):
    if t.get('proto') == 'https':
        print(t['public_url'])
        break
" 2>/dev/null || true)"
  if [[ -n "${PUBLIC}" ]]; then
    break
  fi
  sleep 0.25
done

if [[ -z "${PUBLIC}" ]]; then
  echo "error: ngrok started but public URL not ready — check http://127.0.0.1:4040" >&2
  exit 1
fi

WS="${PUBLIC/https:\/\//wss://}"

cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 HoloLab backend is public via ngrok
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  HTTPS base:  ${PUBLIC}
  WSS base:    ${WS}

  Person C (dashboard localhost):
    SSE:     ${PUBLIC}/agui
    Actions: POST ${PUBLIC}/agui/action
    Health:  ${PUBLIC}/healthz

  Person B (Quest / WebXR):
    WS:      ${WS}/ws
    STT:     POST ${PUBLIC}/transcribe

  ngrok inspector: http://127.0.0.1:4040

  Browser / EventSource clients MUST send header:
    ngrok-skip-browser-warning: 1
  (see backend/BROADCAST_NOTE_NGROK.md for CopilotKit / Quest setup)

  Verify from any machine:
    curl -H "ngrok-skip-browser-warning: 1" ${PUBLIC}/healthz
    curl -N -H "ngrok-skip-browser-warning: 1" ${PUBLIC}/agui

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EOF

wait "${NGROK_PID}"
