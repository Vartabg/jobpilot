#!/usr/bin/env bash
# Focus existing JobPilot Command Center, or create exactly one.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARGS=()
[[ "${1:-}" == "--new" || "${1:-}" == "new" ]] && ARGS+=(new)
RESULT="$(osascript "$ROOT/scripts/iterm/command-center.applescript" "${ARGS[@]}" 2>/dev/null || echo "error")"

case "$RESULT" in
  focused) echo "JobPilot — focused existing HUD window" ;;
  created) echo "JobPilot — opened HUD (boot → job search)" ;;
  *)
    echo "JobPilot — could not open iTerm. Is iTerm installed?"
    exit 1
    ;;
esac