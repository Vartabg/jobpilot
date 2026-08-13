#!/usr/bin/env bash
# boot.sh — start JobPilot Chrome CDP + dashboard server.
# Usage: ./scripts/boot.sh [--quiet]
# Stop:  ./scripts/stop.sh
set -euo pipefail

QUIET=false
if [[ "${1:-}" == "--quiet" || "${1:-}" == "-q" ]]; then
  QUIET=true
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PID_DIR="${HOME}/.jobpilot"
PID_FILE="${PID_DIR}/serve.pid"
LOG_FILE="${PID_DIR}/serve.log"
SWIPE_PID_FILE="${PID_DIR}/swipe.pid"
SWIPE_LOG_FILE="${PID_DIR}/swipe.log"
DEBUG_PORT="${JOBPILOT_DEBUG_PORT:-9222}"
SERVE_PORT="${JOBPILOT_SERVE_PORT:-8767}"
SWIPE_PORT="${JOBPILOT_SWIPE_PORT:-8799}"
REMOTE_ACCESS_MODE="${JOBPILOT_REMOTE_ACCESS:-local}"
REMOTE_TOKEN="${JOBPILOT_REMOTE_TOKEN:-}"

# Personal job, profile, and application data stays on loopback unless the
# operator deliberately opts into the one supported remote-access mode.
SERVE_HOST="127.0.0.1"
SWIPE_BIND_HOST="127.0.0.1"
TS_IP=""
case "$REMOTE_ACCESS_MODE" in
  local)
    ;;
  tailscale)
    if (( ${#REMOTE_TOKEN} < 32 )) || [[ "$REMOTE_TOKEN" =~ [^-A-Za-z0-9._~] ]]; then
      echo "ERROR: Tailscale mode requires JOBPILOT_REMOTE_TOKEN with at least" >&2
      echo "32 URL-safe random characters. Generate one with:" >&2
      echo "  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'" >&2
      exit 2
    fi
    if ! command -v tailscale >/dev/null 2>&1; then
      echo "ERROR: JOBPILOT_REMOTE_ACCESS=tailscale, but Tailscale is unavailable." >&2
      exit 2
    fi
    TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    if [[ -z "$TS_IP" || "$TS_IP" != 100.* ]]; then
      echo "ERROR: JOBPILOT_REMOTE_ACCESS=tailscale, but no Tailscale IPv4 address was found." >&2
      exit 2
    fi
    SERVE_HOST="$TS_IP"
    SWIPE_BIND_HOST="$TS_IP"
    echo "" >&2
    echo "!!! WARNING: JOBPILOT REMOTE ACCESS IS ENABLED !!!" >&2
    echo "Dashboard and swipe APIs require the configured remote token" >&2
    echo "on this Tailscale address (${TS_IP})." >&2
    echo "Unset JOBPILOT_REMOTE_ACCESS to restore loopback-only access." >&2
    echo "" >&2
    ;;
  *)
    echo "ERROR: JOBPILOT_REMOTE_ACCESS must be 'local' or 'tailscale'." >&2
    exit 2
    ;;
esac

mkdir -p "$PID_DIR"

# ── Python env ─────────────────────────────────────────────────────────────
if [[ ! -d "$ROOT/.venv" ]]; then
  if $QUIET; then echo "Setting up JobPilot…"; else echo "▶ Creating .venv..."; fi
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -e "$ROOT"
fi
# shellcheck source=/dev/null
source "$ROOT/.venv/bin/activate"
JOBPILOT_BIN="$(command -v jobpilot)"

_process_exists() {  # $1 = numeric pid
  ps -p "$1" -o pid= >/dev/null 2>&1
}

_is_managed_service() {  # $1 = numeric pid, $2 = dashboard|swipe
  local pid="$1" service="$2" process_uid command_line
  process_uid="$(ps -p "$pid" -o uid= 2>/dev/null | tr -d '[:space:]')"
  [[ -n "$process_uid" && "$process_uid" == "$(id -u)" ]] || return 1
  command_line="$(ps -p "$pid" -o command= 2>/dev/null)"
  case "$service" in
    dashboard)
      [[ "$command_line" == *"$JOBPILOT_BIN serve --host "* ]]
      ;;
    swipe)
      [[ "$command_line" == *"$JOBPILOT_BIN gigs swipe --host "* ]]
      ;;
    *)
      return 1
      ;;
  esac
}

_restart_managed_pid() {  # $1 = label, $2 = pid file, $3 = service identity
  local label="$1" pid_file="$2" service="$3" pid attempt
  [[ -f "$pid_file" ]] || return 0

  IFS= read -r pid <"$pid_file" || pid=""
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$pid_file"
    $QUIET || echo "⚠ Removed malformed ${label} pid file"
    return 0
  fi
  if ! _process_exists "$pid"; then
    rm -f "$pid_file"
    $QUIET || echo "⚠ Removed stale ${label} pid file (pid ${pid})"
    return 0
  fi
  if ! _is_managed_service "$pid" "$service"; then
    echo "ERROR: ${label} pid file points to an unverified live process (pid ${pid})." >&2
    echo "Refusing to stop it. Identify the process before removing ${pid_file}." >&2
    return 1
  fi

  # Every boot rebinds verified boot-managed services. That makes changes to
  # local/Tailscale mode, token, host, or port take effect in this invocation.
  if ! kill "$pid" 2>/dev/null && _process_exists "$pid"; then
    echo "ERROR: Could not stop managed ${label} process ${pid}." >&2
    return 1
  fi
  for (( attempt = 0; attempt < 30; attempt++ )); do
    _process_exists "$pid" || break
    sleep 0.1
  done
  if _process_exists "$pid"; then
    echo "ERROR: Managed ${label} process ${pid} did not stop; refusing to replace it." >&2
    return 1
  fi
  rm -f "$pid_file"
  $QUIET || echo "↻ Restarting ${label} with current access settings"
}

_verify_started_service() {  # $1 = label, $2 = pid file, $3 = service, $4 = pid
  local label="$1" pid_file="$2" service="$3" pid="$4" saved_pid=""
  if _process_exists "$pid" && _is_managed_service "$pid" "$service"; then
    return 0
  fi
  IFS= read -r saved_pid <"$pid_file" || true
  [[ "$saved_pid" == "$pid" ]] && rm -f "$pid_file"
  echo "ERROR: ${label} did not stay running with the requested access settings." >&2
  return 1
}

# ── Chrome CDP ─────────────────────────────────────────────────────────────
if curl -fsS --max-time 2 "http://127.0.0.1:${DEBUG_PORT}/json/version" >/dev/null 2>&1; then
  $QUIET || echo "✓ Chrome CDP already up on port ${DEBUG_PORT}"
else
  if $QUIET; then echo "Opening browser helper…"; else echo "▶ Launching Chrome (CDP port ${DEBUG_PORT})..."; fi
  if ! "$ROOT/scripts/launch_chrome.sh"; then
    if $QUIET; then
      echo "  Optional read-only browser context skipped (Chrome unavailable)"
    else
      echo "⚠ Chrome did not start — dashboard and HUD still work"
    fi
  fi
fi

# ── Dashboard server ───────────────────────────────────────────────────────
_restart_managed_pid "Dashboard" "$PID_FILE" "dashboard"
if $QUIET; then echo "Starting dashboard…"; else echo "▶ Starting dashboard on ${SERVE_HOST}:${SERVE_PORT}..."; fi
nohup "$JOBPILOT_BIN" serve --host "$SERVE_HOST" --port "$SERVE_PORT" >>"$LOG_FILE" 2>&1 &
dashboard_pid=$!
echo "$dashboard_pid" >"$PID_FILE"
sleep 2
_verify_started_service "Dashboard" "$PID_FILE" "dashboard" "$dashboard_pid"

# ── Swipe server (phone-first job swiper) ────────────────────────────────────
# The LaunchAgent helper uses the same explicit privacy mode as this script.
# Set it before a restart so boot's local/remote decision cannot be bypassed.
SWIPE_LABEL="com.vartny.jobpilot.swipe"
_restart_managed_pid "Swipe" "$SWIPE_PID_FILE" "swipe"
if launchctl print "gui/${UID}/${SWIPE_LABEL}" >/dev/null 2>&1; then
  if ! launchctl setenv JOBPILOT_REMOTE_ACCESS "$REMOTE_ACCESS_MODE"; then
    echo "ERROR: Could not enforce the swipe privacy mode; refusing to restart it." >&2
    exit 1
  fi
  if [[ "$REMOTE_ACCESS_MODE" == "tailscale" ]]; then
    if ! launchctl setenv JOBPILOT_REMOTE_TOKEN "$REMOTE_TOKEN"; then
      echo "ERROR: Could not pass remote authentication to swipe." >&2
      exit 1
    fi
  elif ! launchctl unsetenv JOBPILOT_REMOTE_TOKEN; then
    echo "ERROR: Could not clear remote authentication from swipe." >&2
    exit 1
  fi
  if ! launchctl kickstart -k "gui/${UID}/${SWIPE_LABEL}" >/dev/null 2>&1; then
    echo "ERROR: Could not restart swipe with the enforced bind host." >&2
    exit 1
  fi
  $QUIET || echo "✓ Swipe managed by always-on LaunchAgent — restarted"
else
  $QUIET || echo "▶ Starting swipe on ${SWIPE_BIND_HOST}:${SWIPE_PORT}  (always-on: ./scripts/install_swipe_launchd.sh)"
  nohup "$JOBPILOT_BIN" gigs swipe --host "$SWIPE_BIND_HOST" --port "$SWIPE_PORT" >>"$SWIPE_LOG_FILE" 2>&1 &
  swipe_pid=$!
  echo "$swipe_pid" >"$SWIPE_PID_FILE"
  sleep 0.2
  _verify_started_service "Swipe" "$SWIPE_PID_FILE" "swipe" "$swipe_pid"
fi

# ── Health ─────────────────────────────────────────────────────────────────
HEALTH_HOST="$SERVE_HOST"
if [[ "$REMOTE_ACCESS_MODE" == "tailscale" ]]; then
  HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 \
    -H "X-JobPilot-Token: ${REMOTE_TOKEN}" \
    "http://${HEALTH_HOST}:${SERVE_PORT}/api/queue" 2>/dev/null || echo "000")"
else
  HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 \
    "http://${HEALTH_HOST}:${SERVE_PORT}/api/queue" 2>/dev/null || echo "000")"
fi
if [[ "$HTTP_CODE" != "200" ]]; then
  if $QUIET; then
    echo "Dashboard isn't responding yet. Check $LOG_FILE"
  else
    echo "✗ Dashboard not responding on http://${HEALTH_HOST}:${SERVE_PORT}/api/queue"
    echo "  Log: $LOG_FILE"
  fi
  exit 1
fi

if $QUIET; then
  echo "✓ JobPilot is ready"
  echo "  Dashboard: http://${HEALTH_HOST}:${SERVE_PORT}/"
  if [[ "$REMOTE_ACCESS_MODE" == "tailscale" ]]; then
    echo "  Authentication: append ?token=\$JOBPILOT_REMOTE_TOKEN when opening remotely"
    echo "  Swipe:     http://${SWIPE_BIND_HOST}:${SWIPE_PORT}/  (authenticated Tailscale)"
  else
    echo "  Swipe:     http://${SWIPE_BIND_HOST}:${SWIPE_PORT}/  (local only)"
  fi
  echo ""
  exit 0
fi

echo ""
echo "  JobPilot is up"
echo "  ─────────────────────────────────────────"
if [[ "$REMOTE_ACCESS_MODE" == "local" ]]; then
  echo "  Dashboard:  http://127.0.0.1:${SERVE_PORT}/"
else
  echo "  Dashboard:  http://${HEALTH_HOST}:${SERVE_PORT}/  (authenticated Tailscale)"
  echo "  First open: append ?token=\$JOBPILOT_REMOTE_TOKEN"
  echo "  Local loopback is not bound while remote access is enabled"
fi
if [[ "$REMOTE_ACCESS_MODE" == "tailscale" ]]; then
  echo "  Swipe:      http://${SWIPE_BIND_HOST}:${SWIPE_PORT}/  (authenticated)"
  python - "$SWIPE_BIND_HOST" "$SWIPE_PORT" <<'PY' 2>/dev/null || true
import os, sys
try:
    import qrcode
except ImportError:
    sys.exit(0)
host, port = sys.argv[1], sys.argv[2]
q = qrcode.QRCode(border=2)
q.add_data(f"http://{host}:{port}/?token={os.environ['JOBPILOT_REMOTE_TOKEN']}")
q.make()
q.print_ascii(invert=True)
PY
else
  echo "  Swipe:      http://${SWIPE_BIND_HOST}:${SWIPE_PORT}/  (local only)"
  echo "  Phone access: JOBPILOT_REMOTE_ACCESS=tailscale ./scripts/boot.sh"
fi
echo "  Chrome CDP: http://127.0.0.1:${DEBUG_PORT}/"
echo ""
echo "  Company slate: jobpilot queue --refresh"
echo "  Health check:  jobpilot doctor --no-bro"
echo "  Stop all:      ./scripts/stop.sh"
echo ""
