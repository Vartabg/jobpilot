#!/usr/bin/env bash
# Sunday-morning summary script. Loaded by the com.vartny.jobpilot.gigs.weekly
# LaunchAgent (see scripts/install_gigs_launchd.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# GIGPILOT_PYTHON overrides the interpreter; default is the repo venv.
VENV_PY="${GIGPILOT_PYTHON:-$ROOT/.venv/bin/python}"
LOG_DIR="$HOME/Library/Logs/jobpilot-gigs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/weekly-$(date +%Y%m%d).log"

[ -f "$HOME/.secrets/api-keys.env" ] && source "$HOME/.secrets/api-keys.env"

# The repo directory doubles as the `jobpilot` package, so imports need its
# parent on PYTHONPATH (same trick as the ./jobpilot runner).
export PYTHONPATH="$(dirname "$ROOT")${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"

{
  echo "============================================================"
  echo "JobPilot gigs weekly summary: $(date)"
  echo "============================================================"
  "$VENV_PY" -m jobpilot.cli gigs weekly-summary
  echo "Exit: $?"
} >> "$LOG" 2>&1

# Push the summary to ntfy if configured
if [[ -n "${NTFY_TOPIC:-}" ]]; then
  SUMMARY=$(tail -50 "$LOG" | grep -E "^(Total|  )" || true)
  if [[ -n "$SUMMARY" ]]; then
    curl -s -X POST "https://ntfy.sh/${NTFY_TOPIC}" \
      -H "Title: JobPilot gigs weekly summary" \
      -H "Priority: default" \
      -H "Tags: chart_with_upwards_trend" \
      -d "$SUMMARY" >/dev/null || true
  fi
fi
