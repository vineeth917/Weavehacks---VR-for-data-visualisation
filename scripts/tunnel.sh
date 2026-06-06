#!/usr/bin/env bash
# Start a cloudflared tunnel so the Quest browser (on WiFi) can reach localhost:8080
# Usage: ./scripts/tunnel.sh
set -e

if ! command -v cloudflared &>/dev/null; then
  echo "cloudflared not found. Install: brew install cloudflared"
  exit 1
fi

echo "Starting tunnel → http://localhost:8080 ..."
cloudflared tunnel --url http://localhost:8080
