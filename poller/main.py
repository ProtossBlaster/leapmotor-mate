"""LeapMotor Mate — vehicle data poller."""
import json
import logging
import os
import pathlib
import threading
import time

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent

import abrp
from client import (LeapmotorMateClient, set_charge_current_min, EmptyStatusError,
                    seed_coord_signs, get_coord_signs)
from state_machine import State
from db import Database
from mqtt import MqttService
from recorder import Recorder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _add_file_log() -> None:
    """Mirror the poller's stdout logs to a small rotating file under the data dir, so the
    Settings → Diagnostics card can show them to a user without digging through container logs.
    Best-effort: a read-only data dir just means no file (stdout logging is unaffected)."""
    try:
        from logging.handlers import RotatingFileHandler
        data_dir = pathlib.Path(os.environ.get("DB_PATH", "/data/leapmotor_mate.db")).parent
        fh = RotatingFileHandler(str(data_dir / "mate-poller.log"),
                                 maxBytes=1_000_000, backupCount=2)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(fh)
    except Exception:  # noqa: BLE001 — never let logging setup crash the poller
        pass


_add_file_log()
log = logging.getLogger("leapmotor_mate")


# Expected state to publish the instant a command succeeds (mirrors the web UI's
# optimistic overlay), so the HA entity flips immediately instead of waiting for the
# next poll. The boost re-poll below then confirms it from the real signals.
_MQTT_OPTIMISTIC = {
    "lock":        ("locked", True),
    "unlock":      ("locked", False),
    "open_trunk":  ("trunk_open", True),
    "close_trunk": ("trunk_open", False),
}
# Seat comfort MQTT buttons → (action, position, level). on=level 3, off=level 0 (kerniger payload).
_SEAT_MQTT = {
    "seat_heat_driver_on":     ("seat_heat", "driver", 3),
    "seat_heat_driver_off":    ("seat_heat", "driver", 0),
    "seat_heat_passenger_on":  ("seat_heat", "copilot", 3),
    "seat_heat_passenger_off": ("seat_heat", "copilot", 0),
    "seat_vent_driver_on":     ("seat_ventilation", "driver", 3),
    "seat_vent_driver_off":    ("seat_ventilation", "driver", 0),
    "seat_vent_passenger_on":  ("seat_ventilation", "copilot", 3),
    "seat_vent_passenger_off": ("seat_ventilation", "copilot", 0),
}
_MQTT_BOOST_S = 60   # after a command, poll fast for a minute so the state syncs quickly

# The MQTT command handler runs on paho's network thread while the poll loop runs on the
# main thread; both use the same Leapmotor API client (one HTTP session). Serialize API
# access between them so requests can't interleave/corrupt each other.
_API_LOCK = threading.Lock()


def _handle_mqtt_command(client, service, db, vin: str, cmd: str, value):
    """Execute a remote MQTT command, then keep HA in sync the same way the web UI
    does: publish the expected state immediately (optimistic) and trigger a fast
    re-poll so the real signals confirm it within seconds. Without this the MQTT
    state only refreshed on the next scheduled poll (up to 30s when parked), which
    is why it looked stale/out of sync. B10 commands the cloud accepts-but-ignores
    (e.g. full A/C off) stay best-effort."""
    api = client._api
    optimistic = _MQTT_OPTIMISTIC.get(cmd)
    try:
        with _API_LOCK:  # serialize against the poll loop's get_status (shared HTTP session)
            if cmd == "lock":          api.lock_vehicle(vin)
            elif cmd == "unlock":      api.unlock_vehicle(vin)
            elif cmd == "open_trunk":  api.open_trunk(vin)
            elif cmd == "close_trunk": api.close_trunk(vin)
            elif cmd == "find_car":    api.find_vehicle(vin)
            elif cmd == "unlock_charger": api.unlock_charger(vin)
            elif cmd == "door_lock":      # single HA lock entity (#37): value = LOCK / UNLOCK
                if str(value).upper() == "LOCK":
                    api.lock_vehicle(vin);   optimistic = ("locked", True)
                elif str(value).upper() == "UNLOCK":
                    api.unlock_vehicle(vin); optimistic = ("locked", False)
                else:
                    return
            elif cmd == "lock_toggle":    # switch flavour (#38: widgets can toggle a switch,
                if str(value).upper() == "ON":      # not a lock): ON = lock, OFF = unlock
                    api.lock_vehicle(vin);   optimistic = ("locked", True)
                elif str(value).upper() == "OFF":
                    api.unlock_vehicle(vin); optimistic = ("locked", False)
                else:
                    return
            elif cmd == "trunk":          # single HA switch toggle (#71): ON = open, OFF = close
                if str(value).upper() == "ON":
                    api.open_trunk(vin);   optimistic = ("trunk_open", True)
                elif str(value).upper() == "OFF":
                    api.close_trunk(vin);  optimistic = ("trunk_open", False)
                else:
                    return
            elif cmd == "climate_cool":
                api.quick_cool(vin);         optimistic = ("climate_on", True)
            elif cmd == "climate_heat":
                api.quick_heat(vin);         optimistic = ("climate_on", True)
            elif cmd == "climate_defrost":
                api.windshield_defrost(vin); optimistic = ("climate_on", True)
            elif cmd == "climate_off":
                # B10 full A/C off: ac_switch with operate=off (drives acSwitch 1938→0;
                # confirmed on-car 2026-06-06). Guarded so an "A/C Off" press when the A/C
                # is already off is a no-op.
                if getattr(service, "last_climate_on", None) is False:
                    return
                api.ac_switch(vin, params={"operate": "off"});  optimistic = ("climate_on", False)
            elif cmd == "climate_vent":
                api._remote_control(vin=vin, action="ac_on",
                    cmd_content='{"circle":"in","mode":"wind","operate":"manual","position":"all","temperature":"26","windlevel":"7","wshld":"0"}')
                optimistic = ("climate_on", True)
            elif cmd == "steering_heat_on":
                api._remote_control(vin=vin, action="steering_wheel_heat", cmd_content='{"level":"2"}')
            elif cmd == "steering_heat_off":
                api._remote_control(vin=vin, action="steering_wheel_heat", cmd_content='{"level":"1"}')
            elif cmd == "mirror_heat_on":
                api._remote_control(vin=vin, action="rearview_mirror_heat", cmd_content='{"value":"2"}')
            elif cmd == "mirror_heat_off":
                api._remote_control(vin=vin, action="rearview_mirror_heat", cmd_content='{"value":"1"}')
            elif cmd in _SEAT_MQTT:
                action, position, level = _SEAT_MQTT[cmd]
                api._remote_control(vin=vin, action=action,
                    cmd_content=json.dumps({"position": position, "level": str(level)}, separators=(",", ":")))
            else:
                return
        log.info("MQTT: executed command %s %s", cmd, value or "")
    except Exception as exc:  # noqa: BLE001
        log.error("MQTT: command %s failed: %s", cmd, exc)
        return

    # Command succeeded → boost a fast re-poll so the car's REAL state reaches HA in a few seconds.
    # We no longer optimistically publish the entity state — HA must never show a faked value that the
    # car then contradicts ("Mate says closed, the car is open"). The boost below brings the truth.
    if optimistic and service:
        # Still keep the "A/C Off" guard's reference (last_climate_on) in sync with what we just sent —
        # it's otherwise only written by a POLL, so right after a Quick Cool/Heat (before the next poll)
        # it held the old "off" value and the following "A/C Off" was silently skipped (#67).
        if optimistic[0] == "climate_on":
            service.last_climate_on = optimistic[1]
    try:
        # Write from a short-lived dedicated connection: this runs on paho's network
        # thread and the poller's shared db connection isn't safe to use cross-thread.
        import sqlite3
        c = sqlite3.connect(db._path, timeout=5.0)
        try:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                      ("boost_until", str(time.time() + _MQTT_BOOST_S)))
            c.commit()
        finally:
            c.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("MQTT: boost trigger failed: %s", exc)


_last_comfort = {}  # vin -> last comfort_state JSON written (skip redundant settings writes)


def _write_comfort_state(db, data):
    """Persist the working comfort STATE sensors (seat/steering/mirror heat) as a small JSON
    in settings, so the web UI — which reads the positions row, not raw signals — can show
    them read-only. The matching remote commands are broken on the B10, but these states are
    real and reflect manual activation. Written only when the value changes."""
    state = {"seat_heat_driver": data.seat_heat_driver,
             "seat_heat_passenger": data.seat_heat_passenger,
             "seat_vent_driver": data.seat_vent_driver,
             "seat_vent_passenger": data.seat_vent_passenger,
             "steering_heat": data.steering_heat,
             "mirror_heat_left": data.mirror_heat_left,
             "mirror_heat_right": data.mirror_heat_right}
    blob = json.dumps(state, separators=(",", ":"))
    if _last_comfort.get(data.vin) == blob:
        return
    db.set_setting(f"comfort_state_{data.vin.lower()}", blob)
    _last_comfort[data.vin] = blob


_OTA_CHECK_INTERVAL = 600   # 10 min — an OTA notice isn't time-critical
_last_ota_check = 0.0


def _maybe_check_ota(db, client):
    """Throttled, best-effort OTA check (scans the account inbox for an update notice). Stored in
    settings for the web (ota_available / ota_title / ota_time). Never raises — a failed check must
    not disturb the poll; it just leaves the previous value."""
    global _last_ota_check
    now = time.time()
    if now - _last_ota_check < _OTA_CHECK_INTERVAL:
        return
    _last_ota_check = now   # set before the call so a slow/broken endpoint can't be hammered
    try:
        with _API_LOCK:
            res = client.check_ota()
    except Exception as e:  # noqa: BLE001
        log.debug("OTA check failed: %s", e)
        return
    if not res:                          # fetch error → keep the last known value
        return
    db.set_setting("ota_available", "1" if res.get("ota") else "0")
    db.set_setting("ota_title", res.get("title") or "")
    db.set_setting("ota_time", str(res.get("time") or ""))


def _mqtt_tick(db, client, data, service):
    """Manage the MQTT bridge each poll cycle: (dis)connect on the enable flag,
    then publish the current state. Returns the (possibly new/None) service."""
    if db.get_setting("mqtt_enabled") != "1" or not db.get_setting("mqtt_broker"):
        if service:
            service.disconnect()
        return None
    if service is None:
        service = MqttService(
            broker=db.get_setting("mqtt_broker"),
            port=db.get_setting("mqtt_port", "1883"),
            username=db.get_setting("mqtt_user") or None,
            password=db.get_secret("mqtt_pass") or None,
            topic_prefix=db.get_setting("mqtt_prefix", "leapmotor"),
            use_tls=db.get_setting("mqtt_tls") == "1",
            tls_insecure=db.get_setting("mqtt_tls_insecure") == "1",
            discovery_enabled=db.get_setting("mqtt_discovery", "1") == "1",
            get_setting=db.get_setting,
        )
        service.on_command = lambda vin, cmd, val: _handle_mqtt_command(client, service, db, vin, cmd, val)
    try:
        service.publish_status(data)
    except Exception as exc:  # noqa: BLE001
        log.error("MQTT: publish failed: %s", exc)
    return service


_NEXT_WP_INTERVAL_S   = 60     # poll ABRP for next waypoint every 60 s while driving
_PREHEAT_DEBOUNCE_S   = 600    # don't re-trigger within 10 min
_PREHEAT_CONFIRM_S    = 180    # wait up to 3 min for the BMS to confirm heating
_last_next_wp_ts: float = 0.0
_preheat_last_ts: float = 0.0
_preheat_sent_ts: float = 0.0  # when the last preheat command was sent (0 = none pending)


def _maybe_preheat(client, db, data, vin: str, mqtt_service) -> None:
    """While driving with an active ABRP plan: trigger battery preconditioning when
    approaching a DC fast charger and the battery is too cold. Best-effort — never raises."""
    global _last_next_wp_ts, _preheat_last_ts
    now = time.time()
    if now - _last_next_wp_ts < _NEXT_WP_INTERVAL_S:
        return
    _last_next_wp_ts = now

    token = db.get_secret("abrp_token")
    wp = abrp.get_next_wp(token)
    if not wp:
        return

    try:
        dist_km = float(db.get_setting("abrp_preheat_dist_km", "20") or 20)
        temp_c  = float(db.get_setting("abrp_preheat_temp_c",  "20") or 20)
    except (TypeError, ValueError):
        dist_km, temp_c = 20.0, 20.0

    # ABRP's own signal OR local proximity+type check
    batt_heat  = bool(wp.get("batt_heat") or wp.get("batt_heat_req"))
    next_wp    = wp.get("next_charge_wp") or {}
    is_dc_fast = next_wp.get("charger_type") == "dc_fast"
    dist_m     = float(next_wp.get("dist_m") or 999_999)
    is_close   = dist_m < dist_km * 1000

    if not (batt_heat or (is_dc_fast and is_close)):
        return
    bat_temp = data.battery_min_temp or 0.0
    if bat_temp >= temp_c:
        return   # already warm enough
    if now - _preheat_last_ts < _PREHEAT_DEBOUNCE_S:
        return   # recently triggered — avoid hammering the car

    charger_name = next_wp.get("name") or ""
    log.info(
        "ABRP preheat: DC fast charger %.1f km away, battery %.0f°C — triggering battery preheat",
        dist_m / 1000, bat_temp,
    )
    try:
        with _API_LOCK:
            client._api.battery_preheat(vin)
        _preheat_last_ts = now
        global _preheat_sent_ts
        _preheat_sent_ts = now   # arm the confirmation watchdog
    except Exception as exc:  # noqa: BLE001
        log.warning("ABRP preheat: battery_preheat command failed: %s", exc)
        return

    # Notify via MQTT so HA automations (→ iPhone / CarPlay) can pick it up
    if mqtt_service:
        mqtt_service.publish_event(vin, "battery_preheat", {
            "batt_temp_c": round(bat_temp, 1),
            "dist_km":     round(dist_m / 1000, 1),
            "charger":     charger_name,
        })


def _check_preheat_confirmation(data, vin: str, mqtt_service) -> None:
    """After a battery_preheat() command, watch signal 1186 (batteryThermalRequest) to
    confirm whether the BMS actually started heating. Clears itself after confirmation or
    timeout. Best-effort — never raises."""
    global _preheat_sent_ts
    if _preheat_sent_ts == 0.0:
        return
    now = time.time()
    age = now - _preheat_sent_ts
    thermal = data.battery_thermal_request   # 0=none 1=cooling 2=heating

    if thermal == 2:
        log.info("ABRP preheat CONFIRMED: BMS is actively heating the battery (signal 1186=2)")
        if mqtt_service:
            mqtt_service.publish_event(vin, "battery_preheat_confirmed", {
                "batt_temp_c": round(data.battery_min_temp or 0, 1),
            })
        _preheat_sent_ts = 0.0
    elif thermal == 1:
        log.info("ABRP preheat: BMS is COOLING (signal 1186=1) — battery already warm, preheat not needed")
        if mqtt_service:
            mqtt_service.publish_event(vin, "battery_preheat_not_needed", {
                "batt_temp_c": round(data.battery_min_temp or 0, 1),
                "reason": "bms_cooling",
            })
        _preheat_sent_ts = 0.0
    elif age > _PREHEAT_CONFIRM_S:
        log.info(
            "ABRP preheat: no BMS response after %.0fs (signal 1186=0) — "
            "battery may already be at target temp or command had no effect",
            age,
        )
        if mqtt_service:
            mqtt_service.publish_event(vin, "battery_preheat_not_needed", {
                "batt_temp_c": round(data.battery_min_temp or 0, 1),
                "reason": "no_bms_response",
            })
        _preheat_sent_ts = 0.0


def load_config(db: "Database") -> dict:
    """Load credentials from DB settings, falling back to env vars (dev mode).
    DB takes precedence over env — same order as the web layer — so a stray
    LEAPMOTOR_USER in the environment (or a mounted .env) can never silently
    switch the poller to a different account than the one set up in the wizard."""
    def _get(key_env: str, key_db: str, default: str = "", secret: bool = False) -> str:
        # decrypt only the DB-sourced value, then fall back to the (plaintext) env var
        val = db.get_secret(key_db) if secret else db.get_setting(key_db)
        return val or os.environ.get(key_env) or default

    return {
        "username":  _get("LEAPMOTOR_USER", "leapmotor_user"),
        "password":  _get("LEAPMOTOR_PASS", "leapmotor_pass", secret=True),
        "pin":       _get("LEAPMOTOR_PIN",  "leapmotor_pin", "", secret=True),
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
    _startup_login = (cfg["username"], cfg["password"], cfg["pin"])
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

    # GPS sign memory (#43): prime from the last authoritative sign we persisted, so a
    # restart (e.g. an HA add-on update) doesn't briefly plot west/south cars in the sea
    # while waiting for the next poll that carries the signed coordinate pair.
    try:
        seed_coord_signs(v.vin,
                         float(db.get_setting("gps_lat_sign", "0") or 0),
                         float(db.get_setting("gps_lon_sign", "0") or 0))
    except (TypeError, ValueError):
        pass
    _persisted_signs = get_coord_signs(v.vin)

    last_relogin = 0.0   # rate-limit guard for session recovery
    mqtt_service = None   # optional MQTT → HA bridge, created lazily when enabled
    empty_status_count = 0  # consecutive "no live signals" responses (car asleep)
    poll_error_count   = 0  # consecutive hard API errors (cloud unreachable) — for quiet logging

    while True:
        try:
            # Account switched in Settings (Logout → new setup): once a *different* complete
            # login is saved, exit with the RELAUNCH code (42) so run.sh re-launches the poller
            # in-process and re-authenticates as the new account — works even with no container
            # restart policy. The logged-out window (cleared creds, setup_complete=0) is skipped
            # by the is_setup_complete() guard. History is keyed by VIN — the same car carries over.
            if db.is_setup_complete():
                _login_now = load_config(db)
                if (_login_now["username"], _login_now["password"], _login_now["pin"]) != _startup_login:
                    log.info("Leapmotor account changed in Settings — restarting poller to re-authenticate")
                    os._exit(42)

            # Apply user-tunable poll cadence + charge-detection floor (Settings) live, each cycle
            try:
                recorder.set_poll_intervals(
                    int(db.get_setting("poll_parked", "30") or 30),
                    int(db.get_setting("poll_driving", "10") or 10),
                )
                set_charge_current_min(float(db.get_setting("charge_detect_min_a", "2.0") or 2.0))
                recorder.set_reconstruct_min_pct(
                    float(db.get_setting("charge_reconstruct_min_pct", "2.0") or 2.0))
            except (TypeError, ValueError):
                pass
            with _API_LOCK:
                data = client.get_status()
            recorder.process(data)
            _write_comfort_state(db, data)
            _check_preheat_confirmation(data, v.vin, mqtt_service)

            # Persist the authoritative GPS sign the moment a signed poll refreshes it (#43),
            # so the next restart starts on dry land. Only writes when it actually changes
            # (essentially once), so there's no per-poll DB churn.
            signs = get_coord_signs(v.vin)
            if signs != _persisted_signs:
                if signs.get("lat"):
                    db.set_setting("gps_lat_sign", str(signs["lat"]))
                if signs.get("lon"):
                    db.set_setting("gps_lon_sign", str(signs["lon"]))
                _persisted_signs = signs

            # Persist the car's configured charge limit (the SoC it will stop at) whenever it
            # changes, so the Overview hero can label the charge ETA with the real % read cheaply
            # from settings — works even when the limit is changed from the official app. Write
            # only on change, like the GPS sign above → no per-poll churn.
            if data.charge_limit_percent is not None and \
                    str(data.charge_limit_percent) != db.get_setting("charge_limit_percent", ""):
                db.set_setting("charge_limit_percent", str(data.charge_limit_percent))

            # OTA / software-update check (scans the account inbox) — throttled, best-effort.
            _maybe_check_ota(db, client)

            # ABRP live telemetry (opt-in, off by default)
            if db.get_setting("abrp_enabled") == "1":
                abrp.send(db.get_secret("abrp_token"), data)
                # Battery preconditioning: trigger before a DC fast charge if enabled
                if (db.get_setting("abrp_preheat_enabled") == "1"
                        and recorder.state == State.DRIVING):
                    _maybe_preheat(client, db, data, v.vin, mqtt_service)

            # MQTT → Home Assistant bridge (opt-in, off by default)
            mqtt_service = _mqtt_tick(db, client, data, mqtt_service)

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
            empty_status_count = 0
            poll_error_count = 0
        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except EmptyStatusError:
            # Car asleep / not reporting live data (or a brief cloud hiccup) — NOT a
            # real failure. Retry at the normal cadence a couple of times in case it's
            # transient, then back off like any offline state. Recovers on its own once
            # the car reports again. (This used to surface as a scary "Poll error:
            # 'signal'" KeyError.) We log the back-off WARNING only once, not every
            # cycle: a parked car can stay asleep for hours and an ever-climbing
            # "after N tries" warning reads like an escalating failure when it isn't.
            empty_status_count += 1
            if empty_status_count >= 3:
                recorder.mark_offline()
            interval = recorder.poll_interval
            if empty_status_count < 3:
                log.info("Vehicle returned no live data (asleep or briefly unavailable) — "
                         "retry %d/3", empty_status_count)
            elif empty_status_count == 3:
                log.warning("Vehicle not reporting live data (car asleep or unavailable) — "
                            "backing off to %ds polling; recovers automatically when the car "
                            "reports again.", interval)
            # already backed off (count > 3): stay quiet so a sleeping car can't spam the log
        except Exception as exc:
            poll_error_count += 1
            recorder.mark_offline()
            interval = recorder.poll_interval
            # With no long offline backoff we keep polling at the user's cadence, so log the first
            # few errors in full then go quiet — a prolonged cloud outage must not spam the log.
            if poll_error_count <= 3:
                log.error("Poll error: %s", exc)
            elif poll_error_count == 4:
                log.warning("Cloud still unreachable — still polling every %ds, quiet from here; "
                            "recovers automatically when it responds.", interval)
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

        # Heartbeat for /healthz: proves the poll loop is alive (written every cycle,
        # even during offline/asleep backoff) regardless of whether the car reported.
        try:
            db.set_setting("last_loop_ts", str(time.time()))
        except Exception:  # noqa: BLE001
            pass

        # Daily DB pruning — opt-in via Settings (positions_retention_days; 0 = keep forever)
        try:
            ret = int(db.get_setting("positions_retention_days", "0") or 0)
            if ret > 0 and time.time() - float(db.get_setting("last_prune_ts", "0") or 0) > 86400:
                db.prune_positions(ret)
                db.set_setting("last_prune_ts", str(time.time()))
        except Exception as exc:  # noqa: BLE001
            log.warning("DB prune skipped: %s", exc)

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
