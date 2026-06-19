# JobPilot iTerm — one command: `jp`
export JOBPILOT_ROOT="${JOBPILOT_ROOT:-$HOME/AI_Workspace/projects/jobpilot}"

# jp              → focus/open the single Command Center window
# jp new|--new    → force a fresh HUD window
# jp <args>       → jobpilot CLI (jp queue --refresh, jp hud --pick, …)
jp() {
  if [[ $# -eq 0 ]]; then
    "$JOBPILOT_ROOT/scripts/iterm/launch-command-center.sh"
    return
  fi
  case "$1" in
    new|--new)
      shift
      "$JOBPILOT_ROOT/scripts/iterm/launch-command-center.sh" --new "$@"
      return
      ;;
    iterm)
      shift
      if [[ "${1:-}" == "new" || "${1:-}" == "--new" ]]; then
        shift
        "$JOBPILOT_ROOT/scripts/iterm/launch-command-center.sh" --new "$@"
      else
        "$JOBPILOT_ROOT/scripts/iterm/launch-command-center.sh" "$@"
      fi
      return
      ;;
  esac
  (cd "$JOBPILOT_ROOT" && ./jobpilot "$@")
}

# Aliases → same entry point or common tasks (no extra windows)
jpcc() { jp; }
jpboot() { (cd "$JOBPILOT_ROOT" && ./scripts/boot.sh); }
jpstop() { (cd "$JOBPILOT_ROOT" && ./scripts/stop.sh); }
jppick() { jp hud --pick; }
jpo()    { open "$JOBPILOT_ROOT/data/outreach/ready-to-send"; }
jpi()    { open "$JOBPILOT_ROOT/data/outreach/ready-to-send/INDEX.md"; }
jps()    {
  local n="${1:-01}"
  local dir
  dir="$(find "$JOBPILOT_ROOT/data/outreach/ready-to-send" -maxdepth 1 -type d -name "${n}-*" | head -1)"
  [[ -n "$dir" ]] && open "$dir" || echo "No package matching: $n"
}

if [[ -n "${ITERM_SESSION_ID:-}" ]] && whence iterm2_print_user_var &>/dev/null; then
  iterm2_print_user_var jpLane "jobpilot"
fi

jphelp() {
  cat <<'HELP'
JobPilot — keep it simple

  jp                 one full-screen HUD window (reuses if already open)
  jp new             force fresh HUD window (same as jp --new)
  jp queue --refresh scan ATS boards
  jp hud --pick      fuzzy-pick a lead and open URL
  jpo                outreach folder (ready-to-send packages)
  jps 01             open outreach package #1
  jpstop             stop dashboard

Inside HUD: ↑↓ move · Tab gigs/jobs · o open · m application kit · q quit

Scroll stuck on old logs? Run jp new once after updating profiles.
iTerm → Profiles → JobPilot · Command Center → Keys:
  enable "Scroll wheel sends arrow keys when in alternate screen mode"
HELP
}