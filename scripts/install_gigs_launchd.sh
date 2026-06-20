#!/usr/bin/env bash
# Usage: ./scripts/install_gigs_launchd.sh [install|uninstall|status]
#
# Installs the gigs-lane LaunchAgents for the logged-in macOS user:
#   com.vartny.jobpilot.gigs.digest — twice daily (8am scan, 5pm scan)
#   com.vartny.jobpilot.gigs.weekly — Sundays at 9am
#
# The labels are distinct from the old standalone com.vartny.gigpilot.* agents
# so both can coexist during a migration (see docs/GIGS_MIGRATION.md).
#
# Two strong pushes per day beats every-2-hour notification noise. Override
# with GIGPILOT_INTERVAL_SECONDS=NNNN to fall back to interval-based runs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.vartny.jobpilot.gigs.digest"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/jobpilot-gigs"

# Weekly summary agent — runs Sundays at 9am
WEEKLY_LABEL="com.vartny.jobpilot.gigs.weekly"
WEEKLY_PLIST="$HOME/Library/LaunchAgents/${WEEKLY_LABEL}.plist"

INTERVAL_SECONDS="${GIGPILOT_INTERVAL_SECONDS:-}"

install_digest_agent() {
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

  if [[ -n "$INTERVAL_SECONDS" ]]; then
    SCHEDULE_BLOCK="<key>StartInterval</key><integer>${INTERVAL_SECONDS}</integer>"
    SCHEDULE_DESC="every ${INTERVAL_SECONDS} seconds (interval mode)"
  else
    SCHEDULE_BLOCK='<key>StartCalendarInterval</key>
  <array>
    <dict>
      <key>Hour</key>
      <integer>8</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
    <dict>
      <key>Hour</key>
      <integer>17</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
  </array>'
    SCHEDULE_DESC="twice daily (8:00am, 5:00pm)"
  fi

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
    <string>${ROOT}/scripts/gigs_digest.sh</string>
  </array>

  ${SCHEDULE_BLOCK}

  <key>RunAtLoad</key>
  <false/>

  <key>StandardOutPath</key>
  <string>${LOG_DIR}/launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/launchd.err.log</string>

  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
</dict>
</plist>
PLIST

  launchctl bootout "gui/${UID}" "$PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${UID}" "$PLIST"
  launchctl enable "gui/${UID}/${LABEL}" >/dev/null 2>&1 || true

  printf "Installed %s\n" "$LABEL"
  printf "Schedule: %s while this macOS user session is active\n" "$SCHEDULE_DESC"
}

install_weekly_agent() {
  cat > "$WEEKLY_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${WEEKLY_LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>${ROOT}/scripts/gigs_weekly.sh</string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>0</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>

  <key>RunAtLoad</key>
  <false/>

  <key>StandardOutPath</key>
  <string>${LOG_DIR}/weekly.out.log</string>

  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/weekly.err.log</string>

  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
</dict>
</plist>
PLIST

  launchctl bootout "gui/${UID}" "$WEEKLY_PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${UID}" "$WEEKLY_PLIST"
  launchctl enable "gui/${UID}/${WEEKLY_LABEL}" >/dev/null 2>&1 || true

  printf "Installed %s (Sundays 9am)\n" "$WEEKLY_LABEL"
}

install_agent() {
  install_digest_agent
  install_weekly_agent
  printf "Logs: %s\n" "$LOG_DIR"
}

uninstall_agent() {
  launchctl bootout "gui/${UID}" "$PLIST" >/dev/null 2>&1 || true
  launchctl bootout "gui/${UID}" "$WEEKLY_PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST" "$WEEKLY_PLIST"
  printf "Uninstalled %s and %s\n" "$LABEL" "$WEEKLY_LABEL"
}

status_agent() {
  echo "=== ${LABEL} ==="
  launchctl print "gui/${UID}/${LABEL}" 2>/dev/null | head -20 || printf "%s is not loaded\n" "$LABEL"
  echo
  echo "=== ${WEEKLY_LABEL} ==="
  launchctl print "gui/${UID}/${WEEKLY_LABEL}" 2>/dev/null | head -20 || printf "%s is not loaded\n" "$WEEKLY_LABEL"
}

case "${1:-install}" in
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  *) printf "Usage: %s [install|uninstall|status]\n" "$0" >&2; exit 1 ;;
esac
