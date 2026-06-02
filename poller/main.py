"""LeapMotor Mate — vehicle data poller."""
import logging
import os
import pathlib
import time

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent

import abrp
from client import LeapmotorMateClient
from db import Database
from recorder import Recorder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("leapmotor_mate")


def load_config(db: "Database") -> dict:
    """Load credentials from DB settings, falling back to env vars (dev mode).
    DB takes precedence over env — same order as the web layer — so a stray
    LEAPMOTOR_USER in the environment (or a mounted .env) can never silently
    switch the poller to a different account than the one set up in the wizard."""
    def _get(key_env: str, key_db: str, default: str = "") -> str:
        return db.get_setting(key_db) or os.environ.get(key_env) or default

    return {
        "username":  _get("LEAPMOTOR_USER", "leapmotor_user"),
        "password":  _get("LEAPMOTOR_PASS", "leapmotor_pass"),
        "pin":       _get("LEAPMOTOR_PIN",  "leapmotor_pin", ""),
        "cert_path": _cert_path("app.crt", "CERT_PATH"),
        "key_path":  _cert_path("app.key", "KEY_PATH"),
    }


def _cert_path(filename: str, env_key: str) -> str:
    """Resolve the app cert/key: explicit env override → wizard-provided /data/certs →
    image-bundled certs/. The wizard writes user-provided certs to /data/certs (persistent),
    so a fresh install works without any cert baked into the image."""
    override = os.environ.get(env_key)
    if override:
        return override
    data_dir = os.environ.get("DATA_CERT_DIR", "/data/certs")
    data_path = os.path.join(data_dir, filename)
    if os.path.exists(data_path):
        return data_path
    return str(_PROJECT_ROOT / "certs" / filename)


def main():
    db_path = os.environ.get("DB_PATH", "leapmotor_mate.db")
    log.info("Starting LeapMotor Mate poller")

    db = Database(db_path)

    # If no env vars set, wait for the setup wizard to complete
    if not os.environ.get("LEAPMOTOR_USER") and not db.is_setup_complete():
        log.info("Waiting for setup wizard...")
        while not db.is_setup_complete():
            time.sleep(5)
        log.info("Setup complete — starting poller")
    cfg = load_config(db)
    _u = cfg["username"]
    _masked = (_u[:3] + "***" + _u[_u.find("@"):]) if "@" in _u else (_u[:3] + "***")
    device_id = db.get_or_create_device_id()
    log.info("Poller authenticating as account: %s | device_id: %s", _masked, device_id)
    client = LeapmotorMateClient(
        username=cfg["username"],
        password=cfg["password"],
        pin=cfg["pin"],
        cert_path=cfg["cert_path"],
        key_path=cfg["key_path"],
        device_id=device_id,
    )

    client.login()
    # Vehicle is known after login; register it in the DB
    from leapmotor_api import LeapmotorApiClient
    v = client._vehicle
    vehicle_id = db.ensure_vehicle(v.vin, v.car_type, getattr(v, "year", None))

    # First run: set battery capacity from per-model default
    # (will be overridable via setup wizard / settings UI)
    if not db.is_setup_complete():
        from db import default_capacity_for
        capacity = default_capacity_for(v.car_type)
        db.set_battery_capacity(capacity)
        log.info(
            "First run: %s default battery capacity set to %.1f kWh "
            "(change in Settings if you have a different variant)",
            v.car_type, capacity,
        )

    # Crash/restart recovery for open trips/charges happens on the first poll inside
    # Recorder._resume_or_close(), which RESUMES a still-ongoing session (avoiding
    # fragmentation) and only closes it if the activity has actually ended.
    recorder = Recorder(db, vehicle_id)

    log.info("Polling VIN %s (vehicle_id=%d)", v.vin, vehicle_id)

    last_relogin = 0.0   # rate-limit guard for session recovery

    while True:
        try:
            # Apply user-tunable poll cadence (Settings) live, each cycle
            try:
                recorder.set_poll_intervals(
                    int(db.get_setting("poll_parked", "30") or 30),
                    int(db.get_setting("poll_driving", "10") or 10),
                )
            except (TypeError, ValueError):
                pass
            data = client.get_status()
            recorder.process(data)
            interval = recorder.poll_interval
            # Boost window (set via POST /api/boost, e.g. an iPhone BT shortcut relayed
            # by HA when you get in the car): poll fast so we catch the trip start that
            # deep sleep would otherwise miss. Only matters while still parked — once
            # DRIVING the state machine already polls at 10s.
            try:
                boost_until = float(db.get_setting("boost_until", "0") or 0)
            except (TypeError, ValueError):
                boost_until = 0.0
            boosting = time.time() < boost_until and interval > 10
            if boosting:
                interval = 10
            log.info(
                "SOC %.1f%% | Range %d km | Speed %.0f km/h | State: %-8s | Gear: %s | Next poll: %ds%s",
                data.soc, data.range_km, data.speed_kmh,
                recorder.state.value, data.gear, interval,
                " (boost)" if boosting else "",
            )
            recorder.mark_online()
            # Optional ABRP live-data push — no-op unless enabled, token set and our
            # integrator api_key is present. Fire-and-forget; never breaks the poll.
            if db.get_setting("abrp_enabled", "0") == "1":
                abrp.push(data, db.get_setting("abrp_token", "") or "")
        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except Exception as exc:
            log.error("Poll error: %s", exc)
            recorder.mark_offline()
            interval = recorder.poll_interval
            # Self-heal: a vanished /tmp account-cert file (or an auth/token/connection
            # drop) makes every poll fail forever — the poller used to just keep erroring.
            # Force a fresh login to re-create the cert. Guarded to ~once/min so a rapid
            # double login can't trip Leapmotor's rate limiter.
            msg = str(exc).lower()
            recoverable = any(s in msg for s in (
                "certificate", "cert", "unauthorized", "token", "login",
                "verification", "connection", "timed out", "timeout", "ssl",
            ))
            if recoverable and time.time() - last_relogin > 60:
                last_relogin = time.time()
                try:
                    log.info("Attempting session recovery (re-login)…")
                    client.relogin()
                    log.info("Session recovered after re-login")
                except Exception as e2:  # noqa: BLE001
                    log.warning("Re-login failed, will retry next cycle: %s", e2)

        # Interruptible sleep: while parked we may be sleeping for minutes, so check the
        # boost flag every few seconds and wake immediately if one is requested.
        deadline = time.time() + interval
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(5.0, remaining))
            if interval > 10:
                try:
                    if float(db.get_setting("boost_until", "0") or 0) > time.time():
                        break   # boost just requested → poll now
                except (TypeError, ValueError):
                    pass

    client.close()
    db.close()


if __name__ == "__main__":
    main()
