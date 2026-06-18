#!/usr/bin/env bash
# stop.sh — stop JobPilot dashboard server (Chrome left running).
set -euo pipefail

PID_FILE="${HOME}/.jobpilot/serve.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "✓ Stopped dashboard (pid $PID)"
  else
    echo "⚠ Stale pid file — process not running"
  fi
  rm -f "$PID_FILE"
else
  echo "⚠ No dashboard pid file (not running via boot.sh)"
fi

echo "  Chrome was not stopped — close manually if you want."