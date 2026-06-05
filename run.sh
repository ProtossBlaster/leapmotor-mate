#!/bin/bash
set -e

# Home Assistant base images run on s6-overlay, which keeps the Supervisor-provided
# environment (including SUPERVISOR_TOKEN) under /run/s6/container_environment
# instead of in the process env. Load it so the optional Wallbox feature can reach
# the HA API as an add-on. (No-op when standalone — the directory won't exist.)
if [ -d /run/s6/container_environment ]; then
  for _f in /run/s6/container_environment/*; do
    [ -f "${_f}" ] && export "$(basename "${_f}")=$(cat "${_f}")"
  done
fi

export DB_PATH="${DB_PATH:-/data/leapmotor_mate.db}"
export CERT_DIR="/app/certs"
# Inside the container the Docker/ingress layer controls exposure, so bind all
# interfaces here. (The code default is 127.0.0.1 for bare `python main.py` runs.)
export WEB_HOST="${WEB_HOST:-0.0.0.0}"

echo "[LeapMotor Mate] Starting..."
echo "[LeapMotor Mate] DB: ${DB_PATH}"
echo "[LeapMotor Mate] Home Assistant API: $([ -n "${SUPERVISOR_TOKEN}" ] && echo "available (add-on mode)" || echo "not available (standalone)")"

# Start poller in background
PYTHONPATH=/app/poller python3 /app/poller/main.py &
POLLER_PID=$!
echo "[LeapMotor Mate] Poller PID: ${POLLER_PID}"

# Start web server in background
PYTHONPATH=/app/web python3 /app/web/main.py &
WEB_PID=$!
echo "[LeapMotor Mate] Web PID: ${WEB_PID}"

# If either service exits, stop the container (HA or Docker will restart it)
wait -n "$POLLER_PID" "$WEB_PID"
EXIT_CODE=$?
echo "[LeapMotor Mate] A service exited (code ${EXIT_CODE}) — stopping"
kill "$POLLER_PID" "$WEB_PID" 2>/dev/null
exit "$EXIT_CODE"
