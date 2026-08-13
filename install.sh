#!/usr/bin/env bash
# JobPilot Installer
# Run with: curl -fsSL https://raw.githubusercontent.com/Vartabg/jobpilot/main/install.sh | bash

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

JOBPILOT_REPOSITORY="https://github.com/Vartabg/jobpilot.git"
JOBPILOT_EXPECTED_BRANCH="main"

say()  { printf "${CYAN}▶${NC} %s\n" "$1"; }
ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}⚠${NC}  %s\n" "$1"; }
die()  { printf "${RED}✗${NC} %s\n" "$1"; exit 1; }

is_canonical_jobpilot_origin() {
  local origin="${1%/}"
  case "$origin" in
    https://github.com/Vartabg/jobpilot|https://github.com/Vartabg/jobpilot.git|\
    git@github.com:Vartabg/jobpilot|git@github.com:Vartabg/jobpilot.git|\
    ssh://git@github.com/Vartabg/jobpilot|ssh://git@github.com/Vartabg/jobpilot.git)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

verify_existing_install() {
  local repo="$1"
  local origin
  local branch
  local worktree_status

  if ! git -C "$repo" rev-parse --is-inside-work-tree &>/dev/null; then
    die "$repo exists but is not a Git working tree. Move it aside and re-run."
  fi

  if ! origin="$(git -C "$repo" remote get-url origin 2>/dev/null)"; then
    die "Existing install has no origin remote. Refusing to update it."
  fi
  if ! is_canonical_jobpilot_origin "$origin"; then
    die "Existing install origin is not the canonical Vartabg/jobpilot repository. Refusing to update it."
  fi

  if ! branch="$(git -C "$repo" symbolic-ref --quiet --short HEAD)"; then
    die "Existing install is in detached-HEAD state. Check out main and re-run."
  fi
  if [[ "$branch" != "$JOBPILOT_EXPECTED_BRANCH" ]]; then
    die "Existing install is not on '$JOBPILOT_EXPECTED_BRANCH'. Refusing to update it."
  fi

  if ! worktree_status="$(git -C "$repo" status --porcelain=v1 --untracked-files=all)"; then
    die "Could not verify the existing install's working-tree status. Refusing to update it."
  fi
  if [[ -n "$worktree_status" ]]; then
    die "Existing install has local changes or untracked files. Preserve or remove them, then re-run."
  fi
}

main() {

echo ""
printf '%bJobPilot Installer%b\n' "$BOLD" "$NC"
echo "──────────────────────────────────────────"
echo ""

# ── 1. macOS check ────────────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  die "JobPilot currently requires macOS. Windows/Linux support is coming."
fi
ok "macOS detected"

# ── 2. Python 3.11+ check ─────────────────────────────────────────────────────
PYTHON=""
for cmd in python3.12 python3.11 python3; do
  if command -v "$cmd" &>/dev/null; then
    if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo ""
  warn "Python 3.11 or later is required but wasn't found."
  echo ""
  echo "  The easiest way to install it:"
  echo "  1. Go to https://www.python.org/downloads/"
  echo "  2. Click 'Download Python 3.12.x' (the big yellow button)"
  echo "  3. Open the downloaded file and follow the installer"
  echo "  4. Come back and run this script again"
  echo ""
  die "Please install Python 3.11+ and re-run this script."
fi
ok "Python found: $($PYTHON --version)"

# ── 3. Git check ──────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  warn "Git is not installed. Installing via Xcode Command Line Tools..."
  xcode-select --install 2>/dev/null || true
  echo "  A dialog should have appeared asking you to install developer tools."
  echo "  After that completes, run this script again."
  die "Please install git (Xcode tools) and re-run."
fi
ok "Git found"

# ── 4. Clone or update ────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/jobpilot"

if [[ -e "$INSTALL_DIR" ]]; then
  verify_existing_install "$INSTALL_DIR"
  say "Updating existing JobPilot install at $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only --quiet origin "$JOBPILOT_EXPECTED_BRANCH"
  ok "Updated"
else
  say "Downloading JobPilot to $INSTALL_DIR..."
  git clone --quiet --branch "$JOBPILOT_EXPECTED_BRANCH" \
    "$JOBPILOT_REPOSITORY" "$INSTALL_DIR"
  ok "Downloaded"
fi

# Pull/clone hooks must not leave changes behind before local code is installed.
verify_existing_install "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ── 5. Virtual environment ────────────────────────────────────────────────────
say "Setting up Python environment..."
"$PYTHON" -m venv .venv
# shellcheck source=/dev/null
source .venv/bin/activate
ok "Python environment ready"

# ── 6. Install JobPilot ───────────────────────────────────────────────────────
say "Installing JobPilot (this takes about a minute)..."
pip install -e . --quiet
ok "JobPilot installed"

# ── 7. Shell activation shortcut ─────────────────────────────────────────────
SHELL_RC=""
if [[ "$SHELL" == *"zsh"* ]]; then
  SHELL_RC="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
  SHELL_RC="$HOME/.bash_profile"
fi

if [[ -n "$SHELL_RC" ]] && ! grep -qF "# >>> jobpilot >>>" "$SHELL_RC" 2>/dev/null; then
  cat >> "$SHELL_RC" << 'SHELLBLOCK'

# >>> jobpilot >>>
# JobPilot — activate Python environment
alias jobpilot-start='source ~/jobpilot/.venv/bin/activate && echo "JobPilot ready. Type jobpilot --help to see commands."'
# <<< jobpilot <<<
SHELLBLOCK
  ok "Added 'jobpilot-start' shortcut to $SHELL_RC"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
printf '%b%bInstallation complete!%b\n' "$GREEN" "$BOLD" "$NC"
echo ""
echo "── Next steps ──────────────────────────────────────────────────────────"
echo ""
echo "  1. Activate JobPilot (do this each time you open a new Terminal window):"
printf '     %bsource ~/jobpilot/.venv/bin/activate%b\n' "$BOLD" "$NC"
echo ""
echo "  2. Set up your profile (just once):"
printf '     %bjobpilot profile --edit%b\n' "$BOLD" "$NC"
echo ""
echo "  3. Run a health check:"
printf '     %bjobpilot doctor%b\n' "$BOLD" "$NC"
echo ""
echo "  Full guide: https://github.com/Vartabg/jobpilot#your-first-10-minutes"
echo "────────────────────────────────────────────────────────────────────────"
echo ""
}

if [[ -z "${BASH_SOURCE[0]:-}" || "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
