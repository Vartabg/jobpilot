#!/usr/bin/env bash
# boot.sh — start JobPilot Chrome CDP + dashboard server.
# Usage: ./scripts/boot.sh
# Stop:  ./scripts/stop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PID_DIR="${HOME}/.jobpilot"
PID_FILE="${PID_DIR}/serve.pid"
LOG_FILE="${PID_DIR}/serve.log"
DEBUG_PORT="${JOBPILOT_DEBUG_PORT:-9222}"
SERVE_PORT="${JOBPILOT_SERVE_PORT:-8767}"

mkdir -p "$PID_DIR"

# ── Python env ─────────────────────────────────────────────────────────────
if [[ ! -d "$ROOT/.venv" ]]; then
  echo "▶ Creating .venv..."
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -e "$ROOT"
fi
# shellcheck source=/dev/null
source "$ROOT/.venv/bin/activate"

# ── Chrome CDP ─────────────────────────────────────────────────────────────
if curl -fsS --max-time 2 "http://127.0.0.1:${DEBUG_PORT}/json/version" >/dev/null 2>&1; then
  echo "✓ Chrome CDP already up on port ${DEBUG_PORT}"
else
  echo "▶ Launching Chrome (CDP port ${DEBUG_PORT})..."
  "$ROOT/scripts/launch_chrome.sh"
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

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✓ Dashboard already running (pid $(cat "$PID_FILE"))"
else
  echo "▶ Starting dashboard on ${SERVE_HOST}:${SERVE_PORT}..."
  nohup jobpilot serve --host "$SERVE_HOST" --port "$SERVE_PORT" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  sleep 2
fi

# ── Health ─────────────────────────────────────────────────────────────────
HEALTH_HOST="$SERVE_HOST"
HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://${HEALTH_HOST}:${SERVE_PORT}/api/queue" 2>/dev/null || echo "000")"
if [[ "$HTTP_CODE" != "200" ]]; then
  echo "✗ Dashboard not responding on http://${HEALTH_HOST}:${SERVE_PORT}/api/queue"
  echo "  Log: $LOG_FILE"
  exit 1
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
echo "  Chrome CDP: http://127.0.0.1:${DEBUG_PORT}/"
echo ""
echo "  Apply assist:  jobpilot start"
echo "  Health check:  jobpilot doctor --no-bro"
echo "  Stop all:      ./scripts/stop.sh"
echo ""