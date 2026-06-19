#!/usr/bin/env bash
# Outreach lane — list ready-to-send packages (human pushes Send)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/data/outreach/ready-to-send"
cd "$ROOT"
# shellcheck source=/dev/null
[[ -f "$ROOT/.venv/bin/activate" ]] && source "$ROOT/.venv/bin/activate"

cat <<EOF
╔══════════════════════════════════════════════════════════════╗
║  JobPilot Outreach — packages ready (you push Send/Submit)  ║
╚══════════════════════════════════════════════════════════════╝

Folder: $OUT

Commands:
  jpo              open outreach folder in Finder
  jpi              open INDEX.md
  jps 01           open package 01-servicing-copilot
  jpc              open Civitech apply card (data acquisition)
  jobpilot hud --pick   fzf pick any gig/job URL

EOF

if [[ -f "$OUT/INDEX.md" ]]; then
  echo "── INDEX (priority) ──"
  sed -n '1,20p' "$OUT/INDEX.md"
fi

exec "${SHELL:-/bin/zsh}" -l