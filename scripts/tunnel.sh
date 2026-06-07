#!/usr/bin/env bash
# Expose local HoloLab backend (default :8080) via a Cloudflare quick tunnel.
# Prints the public https://*.trycloudflare.com URL to stdout.
#
# Usage:
#   ./scripts/tunnel.sh          # tunnel → localhost:8080
#   PORT=9000 ./scripts/tunnel.sh
#
set -euo pipefail
PORT="${PORT:-8080}"
URL="http://127.0.0.1:${PORT}"

if ! curl -sf "${URL}/healthz" >/dev/null 2>&1; then
  echo "error: backend not reachable at ${URL}/healthz — start uvicorn first" >&2
  exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "error: cloudflared not found — brew install cloudflared" >&2
  exit 1
fi

echo "tunneling ${URL} → public HTTPS (Ctrl-C to stop)" >&2
echo "note: --no-chunked-encoding helps SSE flush through trycloudflare" >&2
exec cloudflared tunnel --no-chunked-encoding --url "${URL}"
