#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
[[ -f "$ROOT/.venv/bin/activate" ]] && source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$(dirname "$ROOT")${PYTHONPATH:+:$PYTHONPATH}"
exec ./jobpilot board --austin --autonomous --watch --limit 25