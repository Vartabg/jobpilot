#!/usr/bin/env bash
# Run the phone job-swiper, keeping the Mac awake while it serves.
# Used by the com.vartny.jobpilot.swipe LaunchAgent (RunAtLoad + KeepAlive),
# so the swiper is always up whenever the Mac is on — reach it from the phone
# anywhere via Tailscale. Keep the Mac plugged in (caffeinate -i blocks idle
# sleep on battery too).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="${GIGPILOT_PYTHON:-$ROOT/.venv/bin/python}"
PORT="${JOBPILOT_SWIPE_PORT:-8799}"

# Secrets (NTFY etc.) if present — harmless for the swiper, kept for parity.
[ -f "$HOME/.secrets/api-keys.env" ] && source "$HOME/.secrets/api-keys.env"

# The repo dir doubles as the `jobpilot` package, so its parent goes on the path.
export PYTHONPATH="$(dirname "$ROOT")${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"

# caffeinate -i: prevent idle system sleep for as long as the server runs.
exec caffeinate -i "$VENV_PY" -m jobpilot.cli gigs swipe --host 0.0.0.0 --port "$PORT"
