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

mkdir -p "$PID_DIR"

# ── Python env ─────────────────────────────────────────────────────────────
if [[ ! -d "$ROOT/.venv" ]]; then
  if $QUIET; then echo "Setting up JobPilot…"; else echo "▶ Creating .venv..."; fi
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -e "$ROOT"
fi
# shellcheck source=/dev/null
source "$ROOT/.venv/bin/activate"

# ── Chrome CDP ─────────────────────────────────────────────────────────────
if curl -fsS --max-time 2 "http://127.0.0.1:${DEBUG_PORT}/json/version" >/dev/null 2>&1; then
  $QUIET || echo "✓ Chrome CDP already up on port ${DEBUG_PORT}"
else
  if $QUIET; then echo "Opening browser helper…"; else echo "▶ Launching Chrome (CDP port ${DEBUG_PORT})..."; fi
  if ! "$ROOT/scripts/launch_chrome.sh"; then
    if $QUIET; then
      echo "  Browser helper skipped (Chrome unavailable — apply assist needs it later)"
    else
      echo "⚠ Chrome did not start — dashboard and HUD still work"
    fi
  fi
fi

# ── Dashboard server ───────────────────────────────────────────────────────
TS_IP=""
if command -v tailscale >/dev/null 2>&1; then
  TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
fi
SERVE_HOST="127.0.0.1"
if [[ -n "$TS_IP" && "$TS_IP" == 100.* ]]; then
  SERVE_HOST="$TS_IP"
fi
# LAN address (same-WiFi path to the phone — the most reliable for the swiper).
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  $QUIET || echo "✓ Dashboard already running (pid $(cat "$PID_FILE"))"
else
  if $QUIET; then echo "Starting dashboard…"; else echo "▶ Starting dashboard on ${SERVE_HOST}:${SERVE_PORT}..."; fi
  nohup jobpilot serve --host "$SERVE_HOST" --port "$SERVE_PORT" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  sleep 2
fi

# ── Swipe server (phone-first job swiper) ────────────────────────────────────
if [[ -f "$SWIPE_PID_FILE" ]] && kill -0 "$(cat "$SWIPE_PID_FILE")" 2>/dev/null; then
  $QUIET || echo "✓ Swipe already running (pid $(cat "$SWIPE_PID_FILE"))"
else
  $QUIET || echo "▶ Starting swipe on 0.0.0.0:${SWIPE_PORT} (LAN + Tailscale)..."
  # Bind all interfaces so the phone can reach it over plain WiFi (LAN IP) OR
  # Tailscale — binding only the Tailscale IP left the LAN path dead.
  nohup jobpilot gigs swipe --host 0.0.0.0 --port "$SWIPE_PORT" >>"$SWIPE_LOG_FILE" 2>&1 &
  echo $! >"$SWIPE_PID_FILE"
fi

# ── Health ─────────────────────────────────────────────────────────────────
HEALTH_HOST="$SERVE_HOST"
HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://${HEALTH_HOST}:${SERVE_PORT}/api/queue" 2>/dev/null || echo "000")"
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
  echo "  Swipe:     http://${LAN_IP:-$HEALTH_HOST}:${SWIPE_PORT}/  (open on phone, same WiFi)"
  echo ""
  exit 0
fi

echo ""
echo "  JobPilot is up"
echo "  ─────────────────────────────────────────"
if [[ "$HEALTH_HOST" == "127.0.0.1" ]]; then
  echo "  Dashboard:  http://127.0.0.1:${SERVE_PORT}/"
else
  echo "  Dashboard:  http://${HEALTH_HOST}:${SERVE_PORT}/  (Tailscale/LAN bind)"
  echo "  Local loopback not bound — use Tailscale URL above"
fi
if [[ -n "${TS_IP:-}" && "$HEALTH_HOST" != "$TS_IP" ]]; then
  echo "  Tailscale:  http://${TS_IP}:${SERVE_PORT}/"
fi
# Swipe: prefer the LAN URL (same-WiFi, most reliable), Tailscale as backup.
SWIPE_HOST="${LAN_IP:-$HEALTH_HOST}"
echo "  Swipe:      http://${SWIPE_HOST}:${SWIPE_PORT}/  (scan the QR below on your phone)"
if [[ -n "${TS_IP:-}" && "$TS_IP" != "$SWIPE_HOST" ]]; then
  echo "              http://${TS_IP}:${SWIPE_PORT}/  (backup, via Tailscale anywhere)"
fi
python - "$SWIPE_HOST" "$SWIPE_PORT" <<'PY' 2>/dev/null || true
import sys
try:
    import qrcode
except ImportError:
    sys.exit(0)
host, port = sys.argv[1], sys.argv[2]
q = qrcode.QRCode(border=2)
q.add_data(f"http://{host}:{port}/")
q.make()
q.print_ascii(invert=True)
PY
echo "  Chrome CDP: http://127.0.0.1:${DEBUG_PORT}/"
echo ""
echo "  Apply assist:  jobpilot start"
echo "  Health check:  jobpilot doctor --no-bro"
echo "  Stop all:      ./scripts/stop.sh"
echo ""