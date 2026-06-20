#!/usr/bin/env bash
# Cron-friendly wrapper for the gigs digest. Loads secrets, runs the digest,
# logs output. On a non-zero exit it attempts a phone push even if python died
# before its own failure handler could run (import error, missing venv, OOM, ...).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# GIGPILOT_PYTHON overrides the interpreter; default is the repo venv.
VENV_PY="${GIGPILOT_PYTHON:-$ROOT/.venv/bin/python}"
LOG_DIR="$HOME/Library/Logs/jobpilot-gigs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/digest-$(date +%Y%m%d).log"

# Source secrets (for NTFY_TOPIC, API keys, etc.)
[ -f "$HOME/.secrets/api-keys.env" ] && source "$HOME/.secrets/api-keys.env"

# The repo directory doubles as the `jobpilot` package, so imports need its
# parent on PYTHONPATH (same trick as the ./jobpilot runner).
export PYTHONPATH="$(dirname "$ROOT")${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"

notify_failure() {
  # Fallback failure push. May duplicate the in-process push from the digest
  # command's own except handler — two alerts beat zero when the laptop runs
  # unattended. First try the CLI (works when the venv is healthy), then raw
  # curl (works when python itself is broken). Never fail the script over a
  # failed push.
  local msg="$1"
  "$VENV_PY" -m jobpilot.cli gigs notify-failure "$msg" \
    || { [ -n "${NTFY_TOPIC:-}" ] && curl -s -m 10 -X POST "https://ntfy.sh/$NTFY_TOPIC" \
           -H "Title: JobPilot gigs digest FAILED" -H "Priority: high" -H "Tags: rotating_light" \
           -d "$msg"; } \
    || true
}

{
  echo "============================================================"
  echo "JobPilot gigs digest run: $(date)"
  echo "============================================================"
  set +e
  "$VENV_PY" -m jobpilot.cli gigs digest --top 12 --min-score 60
  rc=$?
  set -e
  echo ""
  echo "Exit: $rc"
  if [ "$rc" -ne 0 ]; then
    notify_failure "JobPilot gigs digest FAILED (exit $rc) — see $LOG"
  fi
  exit "$rc"
} >> "$LOG" 2>&1
