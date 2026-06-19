#!/usr/bin/env bash
# Install JobPilot iTerm2 dynamic profiles + shell hooks
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ITERM_DIR="${HOME}/Library/Application Support/iTerm2/DynamicProfiles"
DEST="${ITERM_DIR}/JobPilot.json"
SRC="${ROOT}/scripts/iterm/JobPilot.dynamicProfiles.json"
ZSHRC="${HOME}/.zshrc"
MARKER="# >>> jobpilot-iterm >>>"
MARKER_END="# <<< jobpilot-iterm <<<"

mkdir -p "$ITERM_DIR"
# The committed profile is a template (paths as __JPROOT__) so no machine-
# specific path ships in the repo; substitute this clone's root on install.
sed "s#__JPROOT__#${ROOT}#g" "$SRC" >"$DEST"
chmod +x "${ROOT}/scripts/iterm/"*.sh

echo "✓ Installed dynamic profiles → $DEST"
echo "  Profiles appear in iTerm within ~5s (or restart iTerm):"
echo "    • JobPilot · Command Center  (HUD)"
echo "    • JobPilot · Radar"
echo "    • JobPilot · Board"
echo "    • JobPilot · Ops"
echo "    • JobPilot · Outreach"

# Shell hooks
if ! grep -q "$MARKER" "$ZSHRC" 2>/dev/null; then
  cat >>"$ZSHRC" <<EOF

$MARKER
source "${ROOT}/scripts/iterm/jobpilot-shell.zsh"
$MARKER_END
EOF
  echo "✓ Appended JobPilot aliases to ~/.zshrc (jpcc, jph, jpo, …)"
else
  echo "✓ ~/.zshrc already sources jobpilot-shell.zsh"
fi

# Suggested hotkey (manual — iTerm won't accept programmatic global hotkeys safely)
cat <<'HOTKEY'

── Daily use (one command) ──
  jp                 focus existing window, or open one if needed
  jp queue --refresh job commands still work: jp <any jobpilot subcommand>

── Optional hotkey ──
  iTerm → Settings → General → Hotkey → Dedicated hotkey window
  Profile: JobPilot · Command Center · Shortcut: ⌃⌥⌘J
  (Runs profile only — prefer `jp` for boot + full-screen HUD)

HOTKEY

echo ""
echo "Run: source ~/.zshrc && jp"