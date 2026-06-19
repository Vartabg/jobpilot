#!/usr/bin/env bash
set -euo pipefail
LOG="${HOME}/.jobpilot/serve.log"
PID="${HOME}/.jobpilot/serve.pid"
echo "── JobPilot serve log ──  (pid: $(cat "$PID" 2>/dev/null || echo 'not running'))"
echo "    tail -f $LOG"
echo ""
touch "$LOG"
exec tail -n 40 -f "$LOG"