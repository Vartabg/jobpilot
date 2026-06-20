#!/usr/bin/env bash
# Usage: ./scripts/install_swipe_launchd.sh [install|uninstall|status]
#
# Installs a LaunchAgent that keeps the phone job-swiper always running:
#   com.vartny.jobpilot.swipe — RunAtLoad + KeepAlive (starts at login,
#   restarts if it crashes), runs under caffeinate so the Mac won't idle-sleep
#   while it serves. Reach it from the phone anywhere via Tailscale.
#
# This is the "keep the Mac on" always-available setup: leave the Mac powered
# (plugged in, lid open or clamshell-with-power) and the swiper is up 24/7,
# with your data staying entirely on your own machine.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.vartny.jobpilot.swipe"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/jobpilot-gigs"

install_agent() {
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>${ROOT}/scripts/swipe_serve.sh</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>ThrottleInterval</key>
  <integer>10</integer>

  <key>StandardOutPath</key>
  <string>${LOG_DIR}/swipe.out.log</string>

  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/swipe.err.log</string>

  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
</dict>
</plist>
PLIST

  launchctl bootout "gui/${UID}" "$PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${UID}" "$PLIST"
  launchctl enable "gui/${UID}/${LABEL}" >/dev/null 2>&1 || true
  printf "Installed %s (always-on; starts at login, restarts on crash)\n" "$LABEL"
  printf "Logs: %s/swipe.{out,err}.log\n" "$LOG_DIR"
}

uninstall_agent() {
  launchctl bootout "gui/${UID}" "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  printf "Uninstalled %s\n" "$LABEL"
}

status_agent() {
  launchctl print "gui/${UID}/${LABEL}" 2>/dev/null | grep -E "state =|pid =|program =" \
    || printf "%s is not loaded\n" "$LABEL"
}

case "${1:-install}" in
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  *) printf "Usage: %s [install|uninstall|status]\n" "$0" >&2; exit 1 ;;
esac
