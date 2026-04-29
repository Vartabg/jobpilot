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

say()  { printf "${CYAN}▶${NC} %s\n" "$1"; }
ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}⚠${NC}  %s\n" "$1"; }
die()  { printf "${RED}✗${NC} %s\n" "$1"; exit 1; }

echo ""
printf "${BOLD}JobPilot Installer${NC}\n"
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
    version=$("$cmd" -c 'import sys; print(sys.version_info[:2])')
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

if [[ -d "$INSTALL_DIR/.git" ]]; then
  say "Updating existing JobPilot install at $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --quiet
  ok "Updated"
else
  say "Downloading JobPilot to $INSTALL_DIR..."
  git clone --quiet https://github.com/Vartabg/jobpilot.git "$INSTALL_DIR"
  ok "Downloaded"
fi

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

# ── 7. Install Chromium for browser automation ────────────────────────────────
say "Installing browser components..."
playwright install chromium --quiet
ok "Browser ready"

# ── 8. Shell activation shortcut ─────────────────────────────────────────────
SHELL_RC=""
if [[ "$SHELL" == *"zsh"* ]]; then
  SHELL_RC="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
  SHELL_RC="$HOME/.bash_profile"
fi

ACTIVATE_LINE="# JobPilot — auto-activate when in jobpilot directory"
if [[ -n "$SHELL_RC" ]] && ! grep -q "jobpilot/activate-jobpilot" "$SHELL_RC" 2>/dev/null; then
  cat >> "$SHELL_RC" << 'SHELLBLOCK'

# JobPilot — activate Python environment
alias jobpilot-start='source ~/jobpilot/.venv/bin/activate && echo "JobPilot ready. Type jobpilot --help to see commands."'
SHELLBLOCK
  ok "Added 'jobpilot-start' shortcut to $SHELL_RC"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
printf "${GREEN}${BOLD}Installation complete!${NC}\n"
echo ""
echo "── Next steps ──────────────────────────────────────────────────────────"
echo ""
echo "  1. Activate JobPilot (do this each time you open a new Terminal window):"
printf "     ${BOLD}source ~/jobpilot/.venv/bin/activate${NC}\n"
echo ""
echo "  2. Set up your profile (just once):"
printf "     ${BOLD}jobpilot profile --edit${NC}\n"
echo ""
echo "  3. Run a health check:"
printf "     ${BOLD}jobpilot doctor${NC}\n"
echo ""
echo "  Full guide: https://github.com/Vartabg/jobpilot#your-first-10-minutes"
echo "────────────────────────────────────────────────────────────────────────"
echo ""
