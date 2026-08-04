#!/usr/bin/env bash
# JobPilot pilot boot — session pack + health. Cloud agents only.
#
# Usage:
#   ./scripts/pilot_boot.sh              # check + print pack + next-step hints
#   ./scripts/pilot_boot.sh --launching  # pack only (caller starts the agent next)
#   ./scripts/pilot_boot.sh --codex      # then: cloud Codex
set -euo pipefail

JOBPILOT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOBPILOT_WORKSPACE="${AI_WORKSPACE:-$HOME/AI_Workspace}"
JOBPILOT_LAUNCH=""
JOBPILOT_QUIET_NEXT=0

for arg in "$@"; do
  case "$arg" in
    --codex) JOBPILOT_LAUNCH="codex" ;;
    --opencode|--local|--ollama|--warm)
      printf '\n  Local / on-device models were removed.\n'
      printf '  Use: jobpilot   (cloud Codex)\n'
      printf '  Or:  Claude / ChatGPT desktop apps.\n\n'
      exit 1
      ;;
    --launching|--quiet-next) JOBPILOT_QUIET_NEXT=1 ;;
    -h|--help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
  esac
done

printf '\n╔══════════════════════════════════════════════════════════╗\n'
printf '║  JobPilot Pilot Boot — %s\n' "$(date '+%Y-%m-%d %H:%M')"
printf '╚══════════════════════════════════════════════════════════╝\n\n'

printf '── 1) AI ──────────────────────────────────────────────────\n'
printf '  ✓  Cloud-only (local Ollama / on-device models abandoned)\n'
printf '  ·  Pilot agent: cloud Codex (or Claude / ChatGPT apps)\n'
printf '  ·  In-app LLM: Gemini if GEMINI_API_KEY is set\n'

printf '\n── 2) JobPilot ────────────────────────────────────────────\n'
cd "$JOBPILOT_ROOT"
if command -v jobpilot >/dev/null 2>&1; then
  jobpilot doctor --no-bro 2>/dev/null | tail -n 25 || jobpilot doctor 2>/dev/null | tail -n 25 || true
else
  if [[ -x "$JOBPILOT_ROOT/.venv/bin/jobpilot" ]]; then
    "$JOBPILOT_ROOT/.venv/bin/jobpilot" doctor --no-bro 2>/dev/null | tail -n 25 || true
  else
    printf '  ⚠  jobpilot not on PATH — activate .venv or: pip install -e .\n'
  fi
fi

_py=""
if command -v jobpilot >/dev/null 2>&1; then
  _py="$(head -1 "$(command -v jobpilot)" | sed 's/^#!//')"
fi
if [[ -z "$_py" || ! -x "$_py" ]]; then
  if [[ -x "$JOBPILOT_ROOT/.venv/bin/python" ]]; then
    _py="$JOBPILOT_ROOT/.venv/bin/python"
  else
    _py="$(command -v python3 || true)"
  fi
fi
if [[ -n "$_py" ]]; then
  provider="$(
    PYTHONPATH="$(dirname "$JOBPILOT_ROOT")${PYTHONPATH:+:$PYTHONPATH}" "$_py" -c \
      'from jobpilot.core import llm_client; print(llm_client.get_provider() or "none")' \
      2>/dev/null \
    || true
  )"
  if [[ -n "${provider:-}" ]]; then
    printf '  ·  llm_client provider: %s\n' "$provider"
  fi
fi

printf '\n── 3) Strategy pins (read these) ──────────────────────────\n'
printf '  • %s\n' "$JOBPILOT_ROOT/data/strategy/SOLUTIONS_LANE_PLAYBOOK.md"
printf '  • %s\n' "$JOBPILOT_ROOT/data/policy.json"
printf '  • %s\n' "$JOBPILOT_ROOT/docs/APPLICATION_FLOW.md"
printf '  • %s\n' "$JOBPILOT_ROOT/docs/LOCAL_PILOT.md"
printf '  • %s\n' "$JOBPILOT_WORKSPACE/RUNBOOK.md  (§Job Applications)"

printf '\n── 4) What to type (plain English) ────────────────────────\n'
cat <<'EOF'
  jobpilot jobs              show jobs you have not applied to
  jobpilot jobs --pretty     same list, easy for you to read
  jobpilot jobs -n 5         only five jobs
  jobpilot gigs now          freelance / contract leads
  jobpilot log "Acme" --title "Solutions Engineer" --status applied
EOF

printf '\n── 5) Tell the AI helper ──────────────────────────────────\n'
cat <<EOF
You help Garo job-search on this Mac.

Always:
1. Start with: jobpilot jobs
2. After each meaningful step, write a short diary line:
     jobpilot note <what you just did in plain English>
   Examples:
     jobpilot note listed open jobs, top is P-1 AI FDE
     jobpilot note drafted DM for Standard Bots, waiting on Garo
3. Do not invent job boards. Do not auto-submit applications.
4. Optimize for hireability + pay in Austin (and remote US). No QoL travel filter.
   Field OEM, DC/critical facilities, semi FSE, cyber/network, solutions SE all open.

Model: cloud Codex (or Claude / ChatGPT). Workspace: $JOBPILOT_ROOT
Diary (another window can watch): jobpilot diary   |   jobpilot watch
EOF

printf '\n── Boot complete ──────────────────────────────────────────\n'
printf 'Docs: %s/docs/LOCAL_PILOT.md\n' "$JOBPILOT_ROOT"

case "$JOBPILOT_LAUNCH" in
  codex)
    if [[ -x "$JOBPILOT_ROOT/scripts/jp-pilot" ]]; then
      printf 'Launching agent…\n\n'
      exec "$JOBPILOT_ROOT/scripts/jp-pilot" --no-boot
    fi
    if command -v codex >/dev/null 2>&1; then
      printf 'Launching: cloud Codex\n\n'
      cd "$JOBPILOT_WORKSPACE"
      JOBPILOT_CODEX_CONFIG_DIR="${CODEX_HOME:-$HOME/.codex}"
      if [[ -f "$JOBPILOT_CODEX_CONFIG_DIR/cloud.config.toml" ]]; then
        exec codex -p cloud
      fi
      exec codex
    fi
    printf 'codex not found on PATH\n' >&2
    exit 1
    ;;
  *)
    if [[ "$JOBPILOT_QUIET_NEXT" -eq 1 ]]; then
      printf 'Starting agent…\n'
    else
      printf 'Next:\n'
      printf '  jobpilot                         # cloud pilot (recommended)\n'
      printf '  Claude / ChatGPT desktop apps    # general work\n\n'
    fi
    ;;
esac
