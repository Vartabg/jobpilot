#!/usr/bin/env bash
# Run the phone job-swiper, keeping the Mac awake while it serves.
# Used by the com.vartny.jobpilot.swipe LaunchAgent (RunAtLoad + KeepAlive),
# so the swiper is always up whenever the Mac is on — reach it from the phone
# anywhere via Tailscale. Keep the Mac plugged in (caffeinate -i blocks idle
# sleep on battery too).
set -euo pipefail

# LaunchAgents receive a minimal PATH; include Homebrew so Tailscale can be
# discovered when this script runs outside an interactive shell.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="${GIGPILOT_PYTHON:-$ROOT/.venv/bin/python}"
PORT="${JOBPILOT_SWIPE_PORT:-8799}"

# Keep the phone workflow available over Tailscale without exposing the
# swiper on every LAN interface. An explicit host override is supported for
# deliberate deployments; the safe fallback is loopback.
HOST="${JOBPILOT_SWIPE_HOST:-}"
if [[ -z "$HOST" ]] && command -v tailscale >/dev/null 2>&1; then
  HOST="$(tailscale ip -4 2>/dev/null | head -1 || true)"
fi
if [[ -z "$HOST" || "$HOST" == "0.0.0.0" || "$HOST" == "::" ]]; then
  HOST="127.0.0.1"
fi

# Secrets (NTFY etc.) if present — harmless for the swiper, kept for parity.
[ -f "$HOME/.secrets/api-keys.env" ] && source "$HOME/.secrets/api-keys.env"

# The repo dir doubles as the `jobpilot` package, so its parent goes on the path.
export PYTHONPATH="$(dirname "$ROOT")${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"

# caffeinate -i: prevent idle system sleep for as long as the server runs.
exec caffeinate -i "$VENV_PY" -m jobpilot.cli gigs swipe --host "$HOST" --port "$PORT"
