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
export CERT_DIR="${DATA_CERT_DIR:-/data/certs}"
export DATA_CERT_DIR="${CERT_DIR}"
export MATE_API_V2=1

# ── Research / data-collection mode (BetaTester add-on) ──────────────────────
# MATE_RESEARCH gates the full-signal capture + REEV build; it's read at runtime
# (web/research.py) and only baked into the image as a default (Dockerfile ARG→ENV,
# default 0). The BetaTester add-on runs the SAME official image as prod and turns
# research ON here via its own `research` add-on option — so beta and prod share ONE
# image and ONE version number, differing only by this runtime switch. In add-on mode
# the Supervisor writes the options to /data/options.json; read that and export the
# flag so both children (poller + web) inherit it. Absent / false (the official add-on,
# which has no such option, and standalone Docker) leaves the baked default untouched.
# python3 is always present (python base image); a missing//invalid file fails closed.
if [ -f /data/options.json ] && \
   python3 -c 'import json,sys; sys.exit(0 if json.load(open("/data/options.json")).get("research") else 1)' 2>/dev/null; then
  export MATE_RESEARCH=1
  echo "[LeapMotor Mate] Research / data-collection mode ON (BetaTester add-on option)"
fi

# Make sure the data directory exists BEFORE anything opens the DB. A standalone
# user (especially via Docker Desktop "Run", which mounts no volume by default)
# would otherwise hit "sqlite3: unable to open database file" and the poller would
# crash on boot. Previously /data was created only as a SIDE EFFECT of the TMPDIR
# mkdir below — now it's explicit. Pair with VOLUME /data in the Dockerfile so the
# data even persists when no -v is given.
if ! mkdir -p "$(dirname "${DB_PATH}")" 2>/dev/null; then
  echo "[LeapMotor Mate] FATAL: cannot create data dir $(dirname "${DB_PATH}") — mount a writable volume at /data"
  exit 1
fi

# Keep the temporary files the Leapmotor API writes — the per-login account TLS cert + key
# (tempfile.mkstemp, suffix -leapmotor-cert.pem / -leapmotor-key.pem) — on the PERSISTENT /data
# volume instead of the container's ephemeral /tmp. A standalone Docker (e.g. on a NAS) wipes
# /tmp on every restart, so those two files would vanish and remote commands would then fail with
# "Could not find the TLS certificate file" (and every restart would force a fresh re-login). /data
# survives restarts. Guarded: if /data/tmp can't be created, TMPDIR is left as-is (falls back to /tmp).
if mkdir -p /data/tmp 2>/dev/null; then
  export TMPDIR=/data/tmp
fi

# ── Supervise / relaunch ─────────────────────────────────────────────────────
# Mate's in-app toggles relaunch the app by exiting with code 42:
#   • "Try the demo" / "Exit demo"   (web/main.py: _restart_container)
#   • Leapmotor account switch        (poller/main.py: re-authenticate)
# We handle that relaunch HERE by re-exec'ing this script, so it works even with
# NO container restart policy — the default for `docker run` and Docker Desktop
# "Run". (Without this, the toggle just stops the container and looks "crashed".)
# Any OTHER exit code = a real stop/crash → propagate it and let the orchestrator
# (HA add-on / Docker restart policy) decide.
RELAUNCH_CODE=42

# The in-app demo flag lives next to the DB on the persistent volume; the "Try the
# demo" button (and the in-demo exit banner) write/remove it. Re-read it on every
# (re-)launch — so an "Exit demo" relaunch correctly drops back to normal mode.
# MATE_DEMO / the demo DB path are passed only to the CHILDREN (not exported into
# this shell), so re-exec re-derives the mode cleanly from the flag. An explicit
# MATE_DEMO=1 (standalone) forces demo regardless of the flag.
DEMO_ACTIVE=""
if [ -n "${MATE_DEMO}" ] && [ "${MATE_DEMO}" != "0" ] && [ "${MATE_DEMO}" != "false" ]; then
  DEMO_ACTIVE=1
elif [ -f "$(dirname "${DB_PATH}")/demo.flag" ]; then
  DEMO_ACTIVE=1
fi

if [ -n "${DEMO_ACTIVE}" ]; then
  # Demo mode: bundled sample data, no account/cloud, web only.
  echo "[LeapMotor Mate] DEMO MODE — generating sample data at /data/demo.db (no account, no cloud)"
  if ! PYTHONPATH=/app/poller MATE_DEMO=1 DB_PATH=/data/demo.db python3 /app/poller/seed_demo.py; then
    echo "[LeapMotor Mate] demo seed failed"; exit 1
  fi
  echo "[LeapMotor Mate] DEMO MODE — starting web only"
  PYTHONPATH=/app/web MATE_DEMO=1 DB_PATH=/data/demo.db python3 /app/web/main.py &
  WEB_PID=$!
  PIDS="${WEB_PID}"
else
  echo "[LeapMotor Mate] Starting..."
  echo "[LeapMotor Mate] DB: ${DB_PATH}"
  echo "[LeapMotor Mate] Home Assistant API: $([ -n "${SUPERVISOR_TOKEN}" ] && echo "available (add-on mode)" || echo "not available (standalone)")"
  PYTHONPATH=/app/poller python3 /app/poller/main.py &
  POLLER_PID=$!
  echo "[LeapMotor Mate] Poller PID: ${POLLER_PID}"
  PYTHONPATH=/app/web python3 /app/web/main.py &
  WEB_PID=$!
  echo "[LeapMotor Mate] Web PID: ${WEB_PID}"
  PIDS="${POLLER_PID} ${WEB_PID}"
fi

# Wait for the first service to exit, then stop its sibling(s) and reap them.
set +e
wait -n ${PIDS}
EXIT_CODE=$?
kill ${PIDS} 2>/dev/null
wait ${PIDS} 2>/dev/null
set -e

if [ "${EXIT_CODE}" = "${RELAUNCH_CODE}" ]; then
  echo "[LeapMotor Mate] Relaunch requested (demo/account toggle) — restarting in-process"
  exec "$0"
fi

echo "[LeapMotor Mate] A service exited (code ${EXIT_CODE}) — stopping"
exit "${EXIT_CODE}"
