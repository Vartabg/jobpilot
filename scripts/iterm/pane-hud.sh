#!/usr/bin/env bash
# JobPilot command center — boot services, then full-screen interactive HUD
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
[[ -f "$ROOT/.venv/bin/activate" ]] && source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$(dirname "$ROOT")${PYTHONPATH:+:$PYTHONPATH}"
BOOT_LOG="${HOME}/.jobpilot/boot.log"
mkdir -p "${HOME}/.jobpilot"

# Boot quietly to a log file — keeps scrollback empty before HUD takes the screen
if ! ./scripts/boot.sh --quiet >>"$BOOT_LOG" 2>&1; then
  echo "Note: some services didn't start — see $BOOT_LOG"
  sleep 1
fi

# iTerm: wipe scrollback + clear screen so scroll wheel can't reveal boot noise
printf '\033]1337;ClearScrollback\033\\' 2>/dev/null || true
clear

exec ./jobpilot hud --watch --plain