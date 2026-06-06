#!/usr/bin/env bash
# USB fallback: reverse-tunnel port 8080 from Quest to laptop.
# Plug Quest via USB, enable Developer Mode on Quest, then run this.
# Usage: ./scripts/adb_reverse.sh
set -e

if ! command -v adb &>/dev/null; then
  echo "adb not found. Install via Android SDK Platform Tools."
  exit 1
fi

echo "Devices:"
adb devices

echo ""
echo "Reversing tcp:8080 → localhost:8080 on Quest ..."
adb reverse tcp:8080 tcp:8080

echo ""
echo "Done. Open http://localhost:8080 in the Quest browser."
