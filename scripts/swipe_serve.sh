#!/usr/bin/env bash
# Run the job swiper, keeping the Mac awake while it serves.
# Used by the com.vartny.jobpilot.swipe LaunchAgent (RunAtLoad + KeepAlive),
# so the swiper is always up whenever the Mac is on. It stays on loopback
# unless Tailscale access is explicitly enabled. Keep the Mac plugged in
# (caffeinate -i blocks idle sleep on battery too).
set -euo pipefail

# LaunchAgents receive a minimal PATH; include Homebrew so Tailscale can be
# discovered when this script runs outside an interactive shell.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="${GIGPILOT_PYTHON:-$ROOT/.venv/bin/python}"
PORT="${JOBPILOT_SWIPE_PORT:-8799}"

# Login-start is loopback-only even when Tailscale is installed. Deliberate
# phone access requires the same exact opt-in accepted by scripts/boot.sh.
REMOTE_ACCESS_MODE="${JOBPILOT_REMOTE_ACCESS:-local}"
REMOTE_TOKEN="${JOBPILOT_REMOTE_TOKEN:-}"
HOST="127.0.0.1"
case "$REMOTE_ACCESS_MODE" in
  local)
    ;;
  tailscale)
    if (( ${#REMOTE_TOKEN} < 32 )) || [[ "$REMOTE_TOKEN" =~ [^-A-Za-z0-9._~] ]]; then
      echo "ERROR: Tailscale mode requires JOBPILOT_REMOTE_TOKEN with at least" >&2
      echo "32 URL-safe random characters." >&2
      exit 2
    fi
    if ! command -v tailscale >/dev/null 2>&1; then
      echo "ERROR: JOBPILOT_REMOTE_ACCESS=tailscale, but Tailscale is unavailable." >&2
      exit 2
    fi
    HOST="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    if [[ -z "$HOST" || "$HOST" != 100.* ]]; then
      echo "ERROR: JOBPILOT_REMOTE_ACCESS=tailscale, but no Tailscale IPv4 address was found." >&2
      exit 2
    fi
    echo "" >&2
    echo "!!! WARNING: JOBPILOT SWIPE REMOTE ACCESS IS ENABLED !!!" >&2
    echo "The swipe API requires the configured remote token on" >&2
    echo "this Tailscale address (${HOST})." >&2
    echo "Unset JOBPILOT_REMOTE_ACCESS to restore loopback-only access." >&2
    echo "" >&2
    ;;
  *)
    echo "ERROR: JOBPILOT_REMOTE_ACCESS must be 'local' or 'tailscale'." >&2
    exit 2
    ;;
esac

# Secrets (NTFY etc.) if present — harmless for the swiper, kept for parity.
# shellcheck source=/dev/null
[ -f "$HOME/.secrets/api-keys.env" ] && source "$HOME/.secrets/api-keys.env"

# The repo dir doubles as the `jobpilot` package, so its parent goes on the path.
JOBPILOT_PYTHONPATH="$(dirname "$ROOT")${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH="$JOBPILOT_PYTHONPATH"
cd "$ROOT"

# caffeinate -i: prevent idle system sleep for as long as the server runs.
exec caffeinate -i "$VENV_PY" -m jobpilot.cli gigs swipe --host "$HOST" --port "$PORT"
