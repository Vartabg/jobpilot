#!/usr/bin/env bash
# stop.sh — stop JobPilot dashboard + swipe servers (Chrome left running).
set -euo pipefail

_stop() {  # $1 = label, $2 = pid file
  local label="$1" pid_file="$2"
  if [[ -f "$pid_file" ]]; then
    local pid; pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "✓ Stopped ${label} (pid ${pid})"
    else
      echo "⚠ Stale ${label} pid file — process not running"
    fi
    rm -f "$pid_file"
  else
    echo "⚠ No ${label} pid file (not running via boot.sh)"
  fi
}

_stop "dashboard" "${HOME}/.jobpilot/serve.pid"
_stop "swipe" "${HOME}/.jobpilot/swipe.pid"

echo "  Chrome was not stopped — close manually if you want."