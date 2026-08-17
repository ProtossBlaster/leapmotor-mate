"""LeapMotor Mate — vehicle data poller."""
import json
import logging
import os
import pathlib
import threading
import time
from datetime import datetime, timezone

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent

import abrp
import energy_snapshots
import ready_automation
from client import (LeapmotorMateClient, set_charge_current_min, EmptyStatusError,
                    seed_coord_signs, get_coord_signs)
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
        # ~3 MB active + 2 rotated backups ≈ 9 MB retained → covers ≥72 h of continuous driving
        # (~127 h worst-case, right after a rotation), so a shared bundle spans days not minutes.
        fh = RotatingFileHandler(str(data_dir / "mate-poller.log"),
                                 maxBytes=3_000_000, backupCount=2)
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

# The T03's A/C full-off (#67) — see the comment at the climate_off branch below, and the twin in
# web/command_client.py. A named constant on BOTH sides so a test can compare the two VALUES: the two
# trees cannot import each other, and a text search for the literal misses the moment either file
# wraps it across two lines, which is exactly what happened while this was being written.
T03_AC_OFF_BODY = ('{"circle":"out","mode":"wind","operate":"off","position":"all",'
                   '"temperature":"26","windlevel":"3","wshld":"0"}')

# The MQTT command handler runs on paho's network thread while the poll loop runs on the
# main thread; both use the same Leapmotor API client (one HTTP session). Serialize API
# access between them so requests can't interleave/corrupt each other.
_API_LOCK = threading.Lock()


_CLIM_MODE_TOKEN = {1: "cold", 3: "hot", 4: "wind"}   # signal 3713 → ac_on mode; auto(0)/unknown → wind


def _charge_fields(data) -> str:
    """The three inputs that decide whether a charge session opens, for the poll log line.

    @adoewa (#230, 06/08/26): his C10 climbed 49.8% → 90.0% over 3¼ hours with the car demonstrably
    ONLINE — 185 polls, 185 distinct SoC values, frames 2 s old — and Mate opened nothing. The
    bundle could prove the charge happened and could not say WHY it was not recorded, because the
    decision's three inputs were nowhere in the log: the cable's state (1149, via `_is_plugged_in`),
    the decision itself (`_is_charging`), and the pack current (1178). They live in `positions`, one
    row per poll, and in the bundle as a SINGLE snapshot — taken ten hours after the cable came out.

    ⚠️ Measured before adding, over every bundle we hold (7 bundles, 5 cars, 88 car-days): 35
    charges taken while parked, 34 seen, **1 lost**. 2.9%, one car. So this is not a fix for
    something that bites everyone — it is the log carrying enough that the next one is answerable
    at all, whoever it happens to.

    Three fields and no more: this line is written on every poll of every install, ~2900 times a
    day per user. `None` prints as `?`/`—` rather than as 0 — an absent signal is not a zero signal,
    and reading one as the other is a mistake this repo has made before.
    """
    plug = "?" if data.plug_connected is None else (1 if data.plug_connected else 0)
    amps = "—" if data.charge_current_a is None else f"{data.charge_current_a:.1f}"
    return f"plug={plug} chg={data.charging_status} A={amps}"


def _climate_ctx_from_db(db):
    """(mode_token, circle, fan, temp) from the latest stored position — lets an MQTT fan/recirc
    change PRESERVE the rest of the panel. Short-lived connection: this runs on paho's network
    thread and the poller's shared db connection is not safe cross-thread."""
    mode, circle, fan, temp = "wind", "out", 3, 26
    try:
        import sqlite3
        c = sqlite3.connect(db._path, timeout=5.0)
        try:
            row = c.execute("SELECT climate_mode, recirculation, fan_level, climate_target_temp "
                            "FROM positions ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            c.close()
        if row:
            mode = _CLIM_MODE_TOKEN.get(row[0], "wind")
            circle = "in" if row[1] else "out"
            try: fan = max(1, min(int(row[2] or 3), 7))
            except (TypeError, ValueError): fan = 3
            try: temp = max(18, min(int(float(row[3] or 26)), 32))
            except (TypeError, ValueError): temp = 26
    except Exception:  # noqa: BLE001 — fall back to safe defaults
        pass
    return mode, circle, fan, temp


# Windows open %→native scale (mirrors web/command_client._windows_native): the B10/C10/B05
# report "fully open" as 10, the T03 as 100. The quick MQTT "Open" button vents to 20%.
_WINDOWS_SCALE = {"B10": 10, "C10": 10, "B05": 10}   # car_type → native "fully open"; default 100


def _mqtt_car_type(client, vin: str = "") -> str:
    """The MODEL of the car an MQTT command is FOR — from the VIN in its own topic.

    🔴 Model-dependent payloads used to read `client._vehicle`, the FIRST car on the account, while
    the command carries its own VIN in the topic. With a B10 (windows fully open = 10) next to a T03
    (= 100), "open the windows" sent to the T03 was scaled by the B10's rule and opened them a tenth
    of the way. That was fixed for the windows alone and left in A/C-off eight lines below, which is
    why this now lives in ONE place: the next model-dependent command must find it already here.
    An empty or unknown VIN keeps the old behaviour rather than guessing."""
    veh = None
    if vin:
        veh = next((v for v in (getattr(client, "_vehicles", None) or [])
                    if (getattr(v, "vin", "") or "").lower() == vin.lower()), None)
    return (getattr(veh or getattr(client, "_vehicle", None), "car_type", "") or "").upper()


def _mqtt_windows_native(client, pct: int, vin: str = "") -> str:
    """The native window value for the car the command is FOR."""
    full = _WINDOWS_SCALE.get(_mqtt_car_type(client, vin), 100)
    try:
        pct = max(0, min(int(pct), 100))
    except (TypeError, ValueError):
        pct = 0
    return str(round(pct / 100 * full))


def _set_charge_limit_preserving(api, vin: str, pct: int):
    """Change ONLY the charge-limit SoC, preserving the car's existing charge plan — the MQTT twin of
    web/command_client.set_charge_limit.

    NOT the lib's api.set_charge_limit: that one guards on `cycles`, so for an ENABLED
    start-time-only plan (the cloud omits cycles for those) it falls into an all-defaults branch that
    DISABLES the schedule and resets starttime to 00:00 (leapmotor-api #18). That was fixed web-side
    in v2.5.8, but THIS path still called the lib directly — so changing the limit from the Home
    Assistant number could silently wipe a start-time-only plan. Round-trip the current plan through
    set_charge_schedule, preserving enable/window/cycles/circulation/recharge; only the SoC moves."""
    cur = api.get_charge_schedule(vin) or {}
    return api.set_charge_schedule(
        vin,
        enabled=bool(int(cur.get("chargeEnable", 0) or 0)),
        soc_limit=int(pct),
        start_time=cur.get("starttime") or "00:00",
        end_time=cur.get("endtime") or "08:00",
        cycles=cur.get("cycles") or "1,1,1,1,1,1,1",
        circulation=int(cur.get("circulation", 1) or 0),
        recharge=int(cur.get("recharge", 0) or 0),
    )


def _apply_charge_schedule(api, db, vin: str, payload: dict):
    """Apply a PARTIAL charge-schedule update coming from MQTT (#151, @chengler): read the car's
    current plan, override only the keys present in `payload`, write the whole thing back.

    Keys, all optional: `start` "HH:MM" · `stop` "HH:MM" · `soc` 50-100 · `active` true/false ·
    `days` "1,1,1,1,1,1,1" (Monday-first mask). A key you DON'T send keeps its current value, so an
    automation can send just {"start": "23:00"} without disturbing the rest — and this never goes
    through the lib's all-defaults branch that wipes the plan (see _set_charge_limit_preserving).

    The target SoC is the one exception. The cloud's schedule payload doesn't reliably name the SoC
    field, so rather than guess it (and risk silently moving someone's target) we fall back to the
    limit the poller last read FROM THE CAR — `charge_limit_percent`, stored by the poll loop — and
    refuse the command outright if even that is unknown (it's absent on some models, e.g. T03).

    Returns the applied plan as a dict, or None when the payload was rejected."""
    cur = api.get_charge_schedule(vin) or {}

    soc = payload.get("soc")
    if soc is None:
        soc = db.get_charge_limit_percent(vin)
    try:
        soc = int(float(soc))
    except (TypeError, ValueError):
        log.warning("MQTT: charge_schedule has no target soc and the car's is unknown — ignored")
        return None
    if not (50 <= soc <= 100):
        log.warning("MQTT: charge_schedule soc %s out of range 50-100 — ignored", soc)
        return None

    def _hhmm(key, current):
        v = payload.get(key)
        if v is None:
            return current
        parts = str(v).strip().split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError(f"{key}={v!r} is not HH:MM")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"{key}={v!r} is not a valid time")
        return f"{h:02d}:{m:02d}"

    try:
        start_time = _hhmm("start", cur.get("starttime") or "00:00")
        end_time = _hhmm("stop", cur.get("endtime") or "08:00")
    except ValueError as e:
        log.warning("MQTT: charge_schedule %s — ignored", e)
        return None

    active = payload.get("active")
    enabled = bool(int(cur.get("chargeEnable", 0) or 0)) if active is None else bool(active)
    days = payload.get("days")
    cycles = str(days) if days is not None else (cur.get("cycles") or "1,1,1,1,1,1,1")

    api.set_charge_schedule(
        vin, enabled=enabled, soc_limit=soc, start_time=start_time, end_time=end_time,
        cycles=cycles, circulation=int(cur.get("circulation", 1) or 0),
        recharge=int(cur.get("recharge", 0) or 0))
    applied = {"start": start_time, "stop": end_time, "soc": soc,
               "active": enabled, "days": cycles}
    # Keep the Overview's cached window in step with what we just wrote (#173) — the periodic
    # re-read is 30 minutes away, and a chip still showing the old times right after an automation
    # moved them looks like a fault rather than like a cache.
    _store_charge_schedule(db, {"chargeEnable": 1 if enabled else 0,
                                "starttime": start_time, "endtime": end_time}, vin)
    log.info("MQTT: charge schedule → %s", applied)
    return applied


def _handle_mqtt_command(client, service, db, vin: str, cmd: str, value):
    """Execute a remote MQTT command, then keep HA in sync the same way the web UI
    does: publish the expected state immediately (optimistic) and trigger a fast
    re-poll so the real signals confirm it within seconds. Without this the MQTT
    state only refreshed on the next scheduled poll (up to 30s when parked), which
    is why it looked stale/out of sync. B10 commands the cloud accepts-but-ignores
    (e.g. full A/C off) stay best-effort."""
    api = client._api
    # Authorise with the PIN of the car this command NAMES (#186, @cookingeek). MQTT is not scoped
    # to a picked car — the topic carries the VIN — so on two cars with different PINs, using the
    # session's own would authenticate one car's command with the other's four digits: a failure
    # that happens only sometimes and never says why. No per-car PIN → the install-wide one.
    try:
        _pin = db.get_operate_pin(vin)
        if _pin:
            api.operation_password = _pin
    except Exception:  # noqa: BLE001 — a PIN lookup must never stop a command from being tried
        pass
    optimistic = _MQTT_OPTIMISTIC.get(cmd)
    try:
        with _API_LOCK:  # serialize against the poll loop's get_status (shared HTTP session)
            if cmd == "lock":          api.lock_vehicle(vin)
            elif cmd == "unlock":      api.unlock_vehicle(vin)
            elif cmd == "open_trunk":  api.open_trunk(vin)
            elif cmd == "close_trunk": api.close_trunk(vin)
            elif cmd == "open_windows":   api.windows(vin, value=_mqtt_windows_native(client, 20, vin))
            elif cmd == "close_windows":  api.windows(vin, value=_mqtt_windows_native(client, 0, vin))
            elif cmd == "open_sunshade":  api.open_sunshade(vin)
            elif cmd == "close_sunshade": api.close_sunshade(vin)
            elif cmd == "find_car":    api.find_vehicle(vin)
            elif cmd == "unlock_charger": api.unlock_charger(vin)
            elif cmd == "charge_limit":   # writable HA number (#77): value = target SoC %
                try:
                    pct = int(float(value))
                except (TypeError, ValueError):
                    log.warning("MQTT: charge_limit value %r not a number — ignored", value)
                    return
                if not (50 <= pct <= 100):
                    log.warning("MQTT: charge_limit %d%% out of range 50-100 — ignored", pct)
                    return
                _set_charge_limit_preserving(api, vin, pct)
            elif cmd == "charge_schedule":  # writable HA text (#151): JSON {start,stop,soc,active,days}
                try:
                    sched = json.loads(value)
                except (TypeError, ValueError):
                    log.warning("MQTT: charge_schedule value %r is not valid JSON — ignored", value)
                    return
                if not isinstance(sched, dict):
                    log.warning("MQTT: charge_schedule value %r is not a JSON object — ignored", value)
                    return
                applied = _apply_charge_schedule(api, db, vin, sched)
                if applied is None:
                    return
                # Echo the plan back as the entity's state, so Home Assistant shows what's actually
                # set instead of a blank box (no extra cloud call — this is what we just wrote).
                service.publish_state(vin, "charge_schedule",
                                      json.dumps(applied, separators=(",", ":")))
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
                # A/C full-OFF. ONLY the T03 diverges (#67): its climate_on signal (1938) stays false
                # even with the A/C on, so the "already-off" guard would block every off, and it ignores
                # BOTH forms that work elsewhere — bare `operate=off` (the B10's) and `operate=close`
                # (what api.ac_off sends, which is what Mate used to send here). What it honours is
                # operate=off inside the FULL seven-field body: verified on-car by @derekzoli
                # (markoceri/leapmotor-api#9), who watched acSwitch go false rather than trusting the
                # code:0 the cloud returns for every one of them. Same literal as
                # web/command_client.T03_AC_OFF_BODY — a test holds the two byte-identical.
                # B10/C10/B05 keep the EXACT original path below (guard + ac_switch operate=off), untouched.
                if _mqtt_car_type(client, vin) == "T03":
                    log.info("A/C-off (MQTT, T03) → cmd 170 full body, operate=off [@derekzoli]")
                    api._remote_control(vin=vin, action="ac_on", cmd_content=T03_AC_OFF_BODY)
                else:
                    if service.climate_on_for(vin) is False:
                        return
                    api.ac_switch(vin, params={"operate": "off"})
                optimistic = ("climate_on", False)
            elif cmd == "climate_vent":
                api._remote_control(vin=vin, action="ac_on",
                    cmd_content='{"circle":"in","mode":"wind","operate":"manual","position":"all","temperature":"26","windlevel":"7","wshld":"0"}')
                optimistic = ("climate_on", True)
            elif cmd == "fan_level":      # writable HA number: value = fan 1-7 (signal 1941)
                try:
                    lvl = max(1, min(int(float(value)), 7))
                except (TypeError, ValueError):
                    log.warning("MQTT: fan_level value %r not a number — ignored", value); return
                m, circ, _f, tmp = _climate_ctx_from_db(db)
                api._remote_control(vin=vin, action="ac_on", cmd_content=json.dumps(
                    {"circle": circ, "mode": m, "operate": "manual", "position": "all",
                     "temperature": str(tmp), "windlevel": str(lvl), "wshld": "0"}, separators=(",", ":")))
                optimistic = ("climate_on", True)
            elif cmd == "recirculation":  # writable HA switch: ON = recirc / OFF = fresh (signal 1943)
                on = str(value).upper() == "ON"
                m, _circ, f, tmp = _climate_ctx_from_db(db)
                api._remote_control(vin=vin, action="ac_on", cmd_content=json.dumps(
                    {"circle": "in" if on else "out", "mode": m, "operate": "manual", "position": "all",
                     "temperature": str(tmp), "windlevel": str(f), "wshld": "0"}, separators=(",", ":")))
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
            service.set_climate_on(vin, optimistic[1])
    try:
        # Write from a short-lived dedicated connection: this runs on paho's network
        # thread and the poller's shared db connection isn't safe to use cross-thread.
        import sqlite3
        c = sqlite3.connect(db._path, timeout=5.0)
        try:
            # Per CAR (#186): a command to the car in the garage must not also wake the one on
            # the motorway — it spends that car's cloud budget for a state it was not asked about.
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                      (f"boost_until_{str(vin).lower()}", str(time.time() + _MQTT_BOOST_S)))
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
_last_ota_log = None        # last logged (ok, scanned, ota) — log only on change, no 10-min spam


_SCHEDULE_REFRESH_INTERVAL = 1800   # 30 min — a charge window is set once and left alone for months
_last_schedule_refresh = 0.0


def _maybe_refresh_charge_schedule(db, client):
    """Cache the car's charge window in settings for the Overview (#173 @rop12770). Throttled and
    best-effort, exactly like the OTA check above; never raises.

    Why cache at all: the window lives in the CAR — no signal in the poll frame carries it — so it
    costs a cloud round-trip, while the Overview redraws itself every 30 s. Reading it per page view
    would mean two extra calls a minute per user for a value that changes about once a month, which
    is how you end up rate-limited (and, on a shared account, evicted). Thirty minutes stale is
    invisible to a human reading a start time; a change made from Mate itself refreshes it at once
    (see _apply_charge_schedule)."""
    global _last_schedule_refresh
    now = time.time()
    if now - _last_schedule_refresh < _SCHEDULE_REFRESH_INTERVAL:
        return
    _last_schedule_refresh = now   # before the call, so a slow endpoint can't be hammered
    with _API_LOCK:
        sched = client.get_charge_schedule()
    _store_charge_schedule(db, sched, getattr(getattr(client, "_vehicle", None), "vin", "") or "")


def _store_charge_schedule(db, sched, vin: str = "") -> None:
    """Write a fetched schedule into settings, under the CAR it belongs to (#186). A failed read
    leaves the previous value alone rather than blanking the chip on one bad call."""
    if not sched:
        return
    try:
        db.set_charge_schedule(vin, bool(int(sched.get("chargeEnable", 0) or 0)),
                               str(sched.get("starttime") or ""), str(sched.get("endtime") or ""))
    except Exception as e:  # noqa: BLE001
        log.debug("Could not store the charge schedule: %s", e)


def _maybe_check_ota(db, client):
    """Throttled, best-effort OTA check (scans the account inbox for an update notice). Stored in
    settings for the web (ota_available / ota_title / ota_time). Never raises — a failed check must
    not disturb the poll; it just leaves the previous value.

    The outcome is logged (at INFO, or WARNING when the inbox can't be read) so a diagnostics
    bundle answers *why* the Overview shows "None" — Leapmotor has no OTA-status signal, Mate can
    only read the account inbox, and a bare "None" otherwise hides three different cases: empty
    inbox, messages-but-no-update, and inbox-unreadable (issue #156). Logged only on change so a
    stable state doesn't repeat every 10 minutes."""
    global _last_ota_check, _last_ota_log
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
    if not res.get("ok"):                # inbox unreadable → keep the last value (check_ota warned)
        return
    scanned, found = res.get("scanned", 0), bool(res.get("ota"))
    sig = (True, scanned, found)
    if sig != _last_ota_log:             # log the outcome once per change, not every cycle
        if found:
            log.info("OTA inbox scan: update message found among %d message(s): %r",
                     scanned, res.get("title"))
        else:
            log.info("OTA inbox scan: %d message(s) in inbox, none is an update notice", scanned)
        _last_ota_log = sig
    db.set_setting("ota_available", "1" if found else "0")
    db.set_setting("ota_title", res.get("title") or "")
    db.set_setting("ota_time", str(res.get("time") or ""))


_BETA_PREFIX_SUFFIX = "_beta"   # what a colliding BetaTester install renames its prefix to


def _handle_mqtt_collision(db, other_id: str, other_is_beta: bool, vin: str):
    """Another Mate is publishing on our topic prefix. Record it so Settings can say so — and, if we
    are the BetaTester build, get out of the way by ourselves.

    Only the BetaTester moves, and this is the whole rule (Silvio's call, 04/08). Its entities are
    the ones nobody has built an automation on yet, and its own description already tells the tester
    it is the guest here; the official install keeps its prefix, its device and every automation
    pointing at it. When two OFFICIAL installs collide, neither can claim to be the real one, so
    nothing moves and the warning is all Mate has to offer.

    Moving is writing the setting: _mqtt_tick notices the changed signature on the next cycle and
    reconnects on the new prefix. Once only — if we already carry the suffix and are STILL colliding,
    a second beta is on the same broker and that one needs a human."""
    prefix = db.get_setting("mqtt_prefix", "leapmotor")
    moved_to = ""
    if _research_enabled() and not prefix.endswith(_BETA_PREFIX_SUFFIX):
        moved_to = prefix + _BETA_PREFIX_SUFFIX
        db.set_setting("mqtt_prefix", moved_to)
        log.warning("MQTT: another Mate holds prefix '%s' — this BetaTester build is moving to '%s'. "
                    "The official install is untouched.", prefix, moved_to)
    db.set_setting("mqtt_collision", json.dumps({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prefix": prefix, "other_beta": bool(other_is_beta), "vin": vin, "moved_to": moved_to,
    }))


def _mqtt_config_sig(db) -> tuple:
    """Everything the live bridge was BUILT with. The settings page answers "Saved — restart not
    needed", and for the enable flag that was true; for everything else it was not. The service was
    created once and never rebuilt, so a changed broker, port, credentials, TLS or topic prefix sat
    in the database doing nothing until MQTT was switched off and on again — and nothing on screen
    said so. Told to a beta tester as advice before it was checked (BetaTester #13), which is how it
    surfaced. Compare this against the live service and reconnect when it moves."""
    return (db.get_setting("mqtt_broker"), db.get_setting("mqtt_port", "1883"),
            db.get_setting("mqtt_user") or "", db.get_secret("mqtt_pass") or "",
            db.get_setting("mqtt_prefix", "leapmotor"), db.get_setting("mqtt_tls"),
            db.get_setting("mqtt_tls_insecure"), db.get_setting("mqtt_discovery", "1"))


def _mqtt_tick(db, client, data, service, vehicle=None, vehicle_id=None):
    """Manage the MQTT bridge each poll cycle: (dis)connect on the enable flag,
    then publish the current state. Returns the (possibly new/None) service."""
    if db.get_setting("mqtt_enabled") != "1" or not db.get_setting("mqtt_broker"):
        if service:
            service.disconnect()
        return None
    sig = _mqtt_config_sig(db)
    if service is not None and getattr(service, "config_sig", None) != sig:
        log.info("MQTT: configuration changed — reconnecting (prefix=%s)", sig[4])
        service.disconnect()
        service = None
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
            # The car's declared abilities gate ability-dependent command buttons in discovery
            # (e.g. no 'Unlock Charge Cable' on a T03, which never declares code 53 — #142).
            abilities=db.get_abilities(),
            # The car MODEL gates model-absent entities in discovery (e.g. no heated-seat / heated-
            # steering entities on a T03, which lacks them despite the firmware declaring them — #144).
            car_type=db.get_car_type(),
            # Who this install is on the broker, and whether it is the BetaTester build — the two
            # facts the collision check below needs. `mate_device_id` already exists (generated at
            # first run), so no new identity is minted for this.
            instance_id=db.get_setting("mate_device_id", ""),
            is_beta=_research_enabled(),
        )
        service.config_sig = sig
        service.on_command = lambda vin, cmd, val: _handle_mqtt_command(client, service, db, vin, cmd, val)
        service.on_collision = lambda other, other_beta, vin: _handle_mqtt_collision(db, other, other_beta, vin)
    try:
        # This CAR's model and declared abilities, not the install's. Discovery is keyed by VIN, so
        # two cars are two Home Assistant devices — gating both on one car's model would put
        # heated-seat entities on the car that has none and hide the charge-cable button from the
        # car that can use it.
        #
        # `absent_temps` is the same idea measured rather than declared (#144): which temperature
        # sensors THIS car has never reported, so the bridge can drop those entities instead of
        # leaving them at `unknown` for ever. Scoped to `vehicle_id` — a T03 with no cabin sensor
        # beside a C10 that has one must not take the C10's entity away. Measured here, where the log
        # is: the bridge takes no DB handle, and a query per poll is nothing beside a cloud call.
        service.publish_status(
            data,
            abilities=getattr(vehicle, "abilities", None) if vehicle is not None else None,
            car_type=getattr(vehicle, "car_type", None) if vehicle is not None else None,
            absent_temps=db.never_reported_temps(vehicle_id),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("MQTT: publish failed: %s", exc)
    return service


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


def _research_enabled() -> bool:
    """True only in the MateBetaTesterOnly build (MATE_RESEARCH baked into that image).
    Off in the normal build → the full-signal capture below is a complete no-op."""
    return os.environ.get("MATE_RESEARCH", "") not in ("", "0", "false", "False", "no")


def reconcile_coord_signs(db, vehicle_id: int, vin: str) -> None:
    """Prime the GPS sign memory, letting the car's OWN HISTORY overrule the stored setting (#232).

    Called once at poller startup. The setting is a single value; before the v3.8.6 guard, one frame
    that arrived with its minus dropped overwrote it, and every restart then primed from the poisoned
    copy — so the car stayed mirrored for good. The position history cannot be poisoned that way, and
    it is already on disk: this repairs such an install with nothing to press and nothing to ask.

    ⚠️ The setting is REWRITTEN when it loses, not merely ignored — the web reads that setting and
    has no history of its own, so leaving it wrong would fix the poller and leave the map at sea.
    """
    stored: dict[str, float] = {}
    for axis in ("lat", "lon"):
        try:
            stored[axis] = float(db.get_setting(f"gps_{axis}_sign", "0") or 0)
        except (TypeError, ValueError):
            stored[axis] = 0.0
    try:
        history = db.dominant_coord_signs(vehicle_id)
    except Exception:  # noqa: BLE001 — a sign we cannot re-derive must never stop the poller
        log.warning("GPS sign: could not read the position history, keeping the stored sign",
                    exc_info=True)
        history = {}

    for axis, was in stored.items():
        now = history.get(axis)
        if now is None or now == was:
            continue
        if was:
            # A stored sign we are overruling — the only case worth a warning, and the line the next
            # #232 gets answered from. NO coordinate in it: the poller log ships inside the bundle.
            log.warning("GPS %s: the stored sign (%+g) disagrees with this car's own history (%+g) "
                        "— trusting the history and correcting it", axis, was, now)
        else:
            # 0 means "never learned one". Adopting the history here is an ANSWER, not a correction,
            # and it is the ordinary state of every install that predates this code — warning about
            # it would put an alarm in the log of every user who has nothing wrong with them.
            log.info("GPS %s: no sign on record, taking %+g from this car's own history", axis, now)
        db.set_setting(f"gps_{axis}_sign", str(now))
        stored[axis] = now

    seed_coord_signs(vin, stored["lat"], stored["lon"])


class VehicleContext:
    """Everything the poll loop knows about ONE car.

    Until now this state lived as local variables of `main()` — a recorder, the GPS signs it had
    persisted, two error counters, the last value of every raw signal. That is exactly right for one
    car and silently wrong for two: a single `empty_status_count` means a sleeping car backs off the
    one that is being driven, and a single `_research_last_sig` makes each car's signals look changed
    because the other one wrote them last.

    So the state moves here first, before any second car exists, and nothing else changes. With one
    car this object holds precisely what the locals held and the loop behaves identically — which is
    what makes the step safe to take on its own, provable by the suite that already exists.

    `next_due` is the one genuinely new field: a monotonic deadline per car, so a car being driven
    (10 s) and one parked (30 s) keep their own cadence instead of the whole round running at the
    speed of the fastest — which would over-poll the parked one for nothing.
    """

    __slots__ = ("vehicle", "vehicle_id", "vin", "recorder", "persisted_signs",
                 "empty_status_count", "poll_error_count", "research_last_sig",
                 "interval", "next_due")

    def __init__(self, db, vehicle, vehicle_id: int):
        self.vehicle = vehicle
        self.vehicle_id = vehicle_id
        self.vin = vehicle.vin
        # Crash/restart recovery for open trips/charges happens on the first poll inside
        # Recorder._resume_or_close(), which RESUMES a still-ongoing session (avoiding
        # fragmentation) and only closes it if the activity has actually ended.
        self.recorder = Recorder(db, vehicle_id)
        self.persisted_signs: dict = {}
        self.empty_status_count = 0   # consecutive "no live signals" responses (car asleep)
        self.poll_error_count = 0     # consecutive hard API errors (cloud unreachable)
        self.research_last_sig: dict = {}   # beta build: last value per signal id, for delta logging
        self.interval = 30.0
        self.next_due = 0.0           # monotonic; 0 = due now, so the first round polls every car


class AccountState:
    """What belongs to the ACCOUNT rather than to any one car.

    One login heals every car at once, so re-login is rate-limited here and not per vehicle — two
    cars failing together must not double the attempts against the very limiter that rate-limit
    exists to respect. The broker connection is the account's too.

    ⚠️ What the MQTT bridge PUBLISHES is per-car — it takes the abilities and the model at
    construction, read single-car. Splitting that is its own piece of work, not this one.
    """

    __slots__ = ("last_relogin", "mqtt_service")

    def __init__(self):
        self.last_relogin = 0.0
        self.mqtt_service = None


def _poll_vehicle(db, client, ctx, acct) -> None:
    """One car, once. Sets `ctx.interval` — how long before this car is due again.

    🔑 Every failure stops HERE. With one car it made no difference whether an exception ended the
    cycle or the car; with two it decides whether a car in a tunnel takes the other one off the
    road with it. Nothing in this function may raise.
    """
    try:
        # Apply user-tunable poll cadence + charge-detection floor (Settings) live, each cycle
        try:
            ctx.recorder.set_poll_intervals(
                int(db.get_setting("poll_parked", "30") or 30),
                int(db.get_setting("poll_driving", "10") or 10),
            )
            set_charge_current_min(float(db.get_setting("charge_detect_min_a", "2.0") or 2.0))
            ctx.recorder.set_reconstruct_min_pct(
                float(db.get_setting("charge_reconstruct_min_pct", "2.0") or 2.0))
        except (TypeError, ValueError):
            pass
        with _API_LOCK:
            data = client.get_status(ctx.vehicle)
        ctx.recorder.process(data)
        _write_comfort_state(db, data)

        # A range-extender reports a fuel tank (signal 3235) → flag it. On the BetaTester build
        # this is what makes the dedicated page/nav appear, even for a car onboarded before the
        # variant existed (no re-setup needed). The flag is written on BOTH builds because the
        # official one needs it too — not to show anything, but to withhold the figures a generator
        # makes meaningless.
        #
        # 🔑 PER CAR, and the write-once guard is what makes that affordable. It used to be one flag
        # for the install: a range-extender and a plain electric car on the same account would have
        # put the REEV pages on both, and withheld the battery-derived figures from the very car
        # they are correct for. The flag says something about a CAR, so it is stored against one.
        # Written explicitly as "0" as well, so an absent key means "never polled" rather than "not
        # a range-extender" — the difference the legacy fallback below turns on.
        _reev_key = f"is_reev_{ctx.vin.lower()}"
        _reev_now = "1" if data.is_reev else "0"
        if db.get_setting(_reev_key, "") != _reev_now:
            db.set_setting(_reev_key, _reev_now)
        # …and the account-level flag stays written, for a web layer that predates the per-car key
        # (they ship together, but a container can be restarted half-updated).
        if data.is_reev and db.get_setting("is_reev", "0") != "1":
            db.set_setting("is_reev", "1")
            # Wording follows the build: the official Mate offers no REEV support, so its log must
            # not name the feature — let alone announce a "REEV view" that will never appear (#141).
            log.info("REEV detected (fuel signal 3235 present) — enabling REEV view"
                     if _research_enabled() else
                     "Fuel signal present — battery-derived figures will be withheld where a "
                     "range-extender makes them meaningless")

        # Research / BetaTester full-signal capture (MateBetaTesterOnly build only). Logs every
        # raw signal that CHANGED value since the last poll → a complete, timestamped history we
        # can later correlate with the tester's logbook to map REEV signals. No-op otherwise.
        if _research_enabled() and data.raw_signals:
            changed = {k: v for k, v in data.raw_signals.items()
                       if str(v) != ctx.research_last_sig.get(k)}
            if changed:
                db.insert_raw_signal_changes(
                    ctx.vehicle_id, data.timestamp_ms or int(time.time() * 1000), changed)
                ctx.research_last_sig.update({k: str(v) for k, v in changed.items()})

        # Persist the authoritative GPS sign the moment a signed poll refreshes it (#43),
        # so the next restart starts on dry land. Only writes when it actually changes
        # (essentially once), so there's no per-poll DB churn.
        signs = get_coord_signs(ctx.vin)
        if signs != ctx.persisted_signs:
            # Per CAR (#186): the sign is learned from THIS car's own history and it is a firmware
            # quirk, so two cars on one account can differ. One shared key meant the second car's
            # learning overwrote the first's — and that car goes back into the sea.
            db.set_gps_signs(ctx.vehicle.vin, str(signs.get("lat") or ""), str(signs.get("lon") or ""))
            ctx.persisted_signs = signs

        # Persist the car's configured charge limit (the SoC it will stop at) whenever it
        # changes, so the Overview hero can label the charge ETA with the real % read cheaply
        # from settings — works even when the limit is changed from the official app. Write
        # only on change, like the GPS sign above → no per-poll churn.
        if data.charge_limit_percent is not None and \
                str(data.charge_limit_percent) != db.get_charge_limit_percent(data.vin):
            db.set_charge_limit_percent(data.charge_limit_percent, data.vin)

        _maybe_refresh_charge_schedule(db, client)

        # Daily ledger of the official lifetime energy/mileage counters + getEC split
        # (silent phase-1 collector) — throttled to 24h, best-effort.
        energy_snapshots.maybe_sample(db, client, ctx.vin, api_lock=_API_LOCK,
                                      vehicle=ctx.vehicle)

        # Ready-triggered "prepare now" automation — fires once per Ready OFF→ON edge,
        # gated on the interior-temperature condition if configured. Best-effort.
        ready_automation.maybe_trigger(db, client, data, api_lock=_API_LOCK,
                                       vehicle=ctx.vehicle)

        # ABRP live telemetry (opt-in, off by default)
        if db.get_setting("abrp_enabled") == "1":
            # THIS car's token (#186). One token for two cars pushed both of them into the same ABRP
            # vehicle — two positions, two SoCs, interleaved. A car with no token sends nothing.
            _tok = db.get_abrp_token(data.vin)
            if _tok:
                abrp.send(_tok, data)

        # MQTT → Home Assistant bridge (opt-in, off by default)
        acct.mqtt_service = _mqtt_tick(db, client, data, acct.mqtt_service, ctx.vehicle,
                                                 ctx.vehicle_id)

        ctx.interval = ctx.recorder.poll_interval
        # Boost window (set via POST /api/boost, e.g. an iPhone BT shortcut relayed
        # by HA when you get in the car): poll fast so we catch the trip start that
        # deep sleep would otherwise miss. Only matters while still parked — once
        # DRIVING the state machine already polls at 10s.
        boosting = db.boosting(ctx.vehicle.vin) and ctx.interval > 10
        if boosting:
            ctx.interval = 10
        # Frame age = wall-clock − the cloud frame's own timestamp (sig sts/1). Fresh ≈ a few
        # seconds; if the cloud re-serves a stale frame (car in a 4G dead zone) it climbs without
        # bound — the one signal that tells "stale re-serve" from "genuinely stopped here".
        frame_age = (f"{(int(time.time() * 1000) - data.timestamp_ms) / 1000:.0f}s"
                     if data.timestamp_ms else "?")
        log.info(
            "SOC %.1f%% | Range %d km | Speed %.0f km/h | Odo %.0f km | State: %-8s | "
            "Gear: %s | %s | Frame age: %s | Next poll: %ds%s",
            data.soc, data.range_km, data.speed_kmh, data.odometer_km,
            ctx.recorder.state.value, data.gear, _charge_fields(data), frame_age, ctx.interval,
            " (boost)" if boosting else "",
        )
        ctx.recorder.mark_online()
        ctx.empty_status_count = 0
        ctx.poll_error_count = 0
    except EmptyStatusError:
        # Car asleep / not reporting live data (or a brief cloud hiccup) — NOT a
        # real failure. Retry at the normal cadence a couple of times in case it's
        # transient, then back off like any offline state. Recovers on its own once
        # the car reports again. (This used to surface as a scary "Poll error:
        # 'signal'" KeyError.) We log the back-off WARNING only once, not every
        # cycle: a parked car can stay asleep for hours and an ever-climbing
        # "after N tries" warning reads like an escalating failure when it isn't.
        ctx.empty_status_count += 1
        if ctx.empty_status_count >= 3:
            ctx.recorder.mark_offline()
        ctx.interval = ctx.recorder.poll_interval
        if ctx.empty_status_count < 3:
            log.info("Vehicle returned no live data (asleep or briefly unavailable) — "
                     "retry %d/3", ctx.empty_status_count)
        elif ctx.empty_status_count == 3:
            log.warning("Vehicle not reporting live data (car asleep or unavailable) — "
                        "backing off to %ds polling; recovers automatically when the car "
                        "reports again.", ctx.interval)
        # already backed off (count > 3): stay quiet so a sleeping car can't spam the log
    except Exception as exc:
        ctx.poll_error_count += 1
        ctx.recorder.mark_offline()
        ctx.interval = ctx.recorder.poll_interval
        # With no long offline backoff we keep polling at the user's cadence, so log the first
        # few errors in full then go quiet — a prolonged cloud outage must not spam the log.
        if ctx.poll_error_count <= 3:
            log.error("Poll error: %s", exc)
        elif ctx.poll_error_count == 4:
            log.warning("Cloud still unreachable — still polling every %ds, quiet from here; "
                        "recovers automatically when it responds.", ctx.interval)
        # Self-heal: a vanished /tmp account-cert file (or an auth/token/connection
        # drop) makes every poll fail forever — the poller used to just keep erroring.
        # Force a fresh login to re-create the cert. Guarded to ~once/min so a rapid
        # double login can't trip Leapmotor's rate limiter.
        msg = str(exc).lower()
        recoverable = any(s in msg for s in (
            "certificate", "cert", "unauthorized", "token", "login",
            "verification", "connection", "timed out", "timeout", "ssl",
        ))
        if recoverable and time.time() - acct.last_relogin > 60:
            acct.last_relogin = time.time()
            try:
                log.info("Attempting session recovery (re-login)…")
                client.relogin()
                log.info("Session recovered after re-login")
            except Exception as e2:  # noqa: BLE001
                log.warning("Re-login failed, will retry next cycle: %s", e2)




def main():
    db_path = os.environ.get("DB_PATH", "leapmotor_mate.db")
    log.info("Starting LeapMotor Mate poller")

    db = Database(db_path)

    # Factory reset requested from Settings: the web side set this marker, cleared the setup gate
    # and relaunched the app (run.sh restarts both processes). The destructive wipe happens HERE,
    # at startup, where the poller is the sole DB writer — so it can't race a concurrent poll — and
    # the wizard-wait below then opens a fresh setup. The wipe clears the marker too (one txn).
    if db.get_setting("factory_reset_pending", "0") == "1":
        log.warning("Factory reset requested — erasing all local data (account, trips, charges, "
                    "positions, settings); the setup wizard will reopen")
        db.factory_reset()

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

    # Startup login with in-process retry + backoff. A transient cloud error here — e.g. the
    # `code 39 "Information verification failed, try again later"` that hits right after a freshly
    # accepted car share (not yet propagated on the backend), or a token/connection blip — used to
    # propagate out of main(), exit the process, and let the entrypoint restart it in a tight storm
    # that hammered the cloud (the poll loop already self-heals via relogin(); only startup didn't).
    # Retry HERE instead so we never exit on a recoverable error. Genuinely bad credentials are not
    # hammered in a tight loop: record a clear status for the setup UI and wait. Either way, a
    # credentials change in the setup wizard restarts the poller at once to apply the new login.
    _login_backoff = 5.0
    while True:
        try:
            client.login()
            db.set_setting("poller_login_error", "")   # clear any stale error on success
            break
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            # Bad credentials won't fix themselves by retrying. Everything else — the transient
            # cloud `code 39`, an unpropagated fresh car-share ("no vehicles found"), cert/token/
            # connection blips — is recoverable: keep retrying with a capped backoff.
            bad_creds = any(s in msg.lower() for s in (
                "password", "incorrect", "wrong account", "account does not exist",
                "invalid account", "user does not exist", "account or password",
            ))
            db.set_setting("poller_login_error", msg[:300])
            log.error("Startup login failed%s: %s",
                      " (check credentials)" if bad_creds else " — will retry", msg)
            # Wait before retrying, but stay responsive to a credentials change made in the setup
            # wizard (fix a typo / re-onboard). On bad creds wait long (no point hammering the
            # cloud); on transient errors retry after the growing backoff.
            wait = 3600.0 if bad_creds else _login_backoff
            waited = 0.0
            while waited < wait:
                time.sleep(min(5.0, wait - waited))
                waited += 5.0
                _now = load_config(db)
                if (_now["username"], _now["password"], _now["pin"]) != _startup_login:
                    log.info("Credentials changed during startup login — restarting to apply")
                    os._exit(42)
            _login_backoff = min(_login_backoff * 2, 300.0)

    # Every car on the account is registered and gets its own context. `ensure_vehicle` is keyed by
    # VIN, so a car that has been here before keeps its id and its whole history.
    from leapmotor_api import LeapmotorApiClient
    v = client._vehicle
    contexts = []
    for veh in (client._vehicles or [v]):
        vid = db.ensure_vehicle(veh.vin, veh.car_type, getattr(veh, "year", None),
                                abilities=getattr(veh, "abilities", None))
        contexts.append(VehicleContext(db, veh, vid))
    # The one-car → two-cars transition is visible HERE and nowhere else, and it is where the
    # install-wide ABRP token stops being safe: from this moment both cars would answer to it and
    # push into a single ABRP vehicle. Hand it to the car that was here first and drop the shared
    # key. No-op with one car, and on every start after the first.
    db.migrate_shared_ready_automation()
    _moved = db.migrate_shared_abrp_token()
    if _moved:
        log.info("ABRP: install-wide token assigned to %s (a second car was registered); "
                 "other cars send nothing until they get their own token", _moved[-6:])
    ctx = contexts[0]
    vehicle_id = ctx.vehicle_id

    # First run: set battery capacity from per-model default
    # (will be overridable via setup wizard / settings UI)
    #
    # ⚠️ The GLOBAL capacity setting still follows the first car — it is the legacy single-car
    # value. Per-car capacity has lived on `vehicles.capacity_kwh` since v2.2.0 and `ensure_vehicle`
    # seeds each additional car from its own model default, which is the number that matters: it is
    # what the written trip and charge energies are computed from. A B10's 65 kWh applied to a T03's
    # 36 would inflate every figure by 80%, permanently, in the rows themselves.
    if not db.is_setup_complete():
        from db import default_capacity_for
        capacity = default_capacity_for(v.car_type)
        db.set_battery_capacity(capacity)
        log.info(
            "First run: %s default battery capacity set to %.1f kWh "
            "(change in Settings if you have a different variant)",
            v.car_type, capacity,
        )

    for c in contexts:
        log.info("Polling VIN %s (vehicle_id=%d, model %s)", c.vin, c.vehicle_id,
                 getattr(c.vehicle, "car_type", "?"))

    # GPS sign memory (#43): prime from the last authoritative sign we persisted, so a
    # restart (e.g. an HA add-on update) doesn't briefly plot west/south cars in the sea
    # while waiting for the next poll that carries the signed coordinate pair.
    #
    # #232: the setting alone is not trustworthy enough to prime from. It is ONE value, and until
    # the v3.8.6 guard a single frame that arrived with its minus dropped overwrote it — after which
    # every reader mirrored the car, restart after restart, because the poisoned value was also what
    # we primed from. rop12770's install is stuck exactly there: eighteen trip starts logged at
    # longitude -7.2, and a gps_lon_sign of +1 written by one bad frame after the last of them.
    #
    # So the car's own position history gets the casting vote. It cannot be poisoned by one frame —
    # you cannot drive a fortnight of kilometres somewhere you have never been — and it is already on
    # disk, which is why this repairs a poisoned install with no button to press and nothing to ask
    # the owner. The guard stops NEW installs being poisoned; this un-poisons the ones that already
    # were. Installing the fix means restarting, and the restart IS the repair.
    for c in contexts:
        reconcile_coord_signs(db, c.vehicle_id, c.vin)
        c.persisted_signs = get_coord_signs(c.vin)

    # Account-level, and staying that way: one login heals every car at once, and the broker
    # connection is the account's, not a car's. ⚠️ What the MQTT bridge PUBLISHES is per-car
    # (abilities and model are read single-car at construction) — that is its own piece of work,
    # not this one.
    last_relogin = 0.0   # rate-limit guard for session recovery
    mqtt_service = None   # optional MQTT → HA bridge, created lazily when enabled

    acct = AccountState()

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

            # Sequential, deliberately: one writer is what keeps SQLite behaving exactly as it does
            # today. Two poller processes would be two writers and SQLITE_BUSY; two threads here
            # would be a race over the same recorder state. Polling car after car costs about a
            # second each and buys the whole problem away.
            #
            # And each car on its OWN clock. Running the round at the fastest car's cadence would
            # poll a parked car every ten seconds because another one is being driven — harmless
            # (reading the cloud does not wake the car) but pointless load on the very session
            # v2.13.2 stopped us exhausting.
            now = time.time()
            for ctx in contexts:
                if ctx.next_due > now:
                    continue
                _poll_vehicle(db, client, ctx, acct)
                ctx.next_due = time.time() + ctx.interval
        except KeyboardInterrupt:
            log.info("Stopped by user")
            break

        # OTA / software-update check (scans the account INBOX, not a car) — throttled,
        # best-effort, and once a round however many cars there are.
        try:
            _maybe_check_ota(db, client)
        except Exception as exc:  # noqa: BLE001
            log.debug("OTA check skipped: %s", exc)

        # Heartbeat for /healthz: proves the poll loop is alive (written every cycle,
        # even during offline/asleep backoff) regardless of whether the car reported.
        try:
            db.set_setting("last_loop_ts", str(time.time()))
        except Exception:  # noqa: BLE001
            pass

        # Daily DB pruning (at most once/day). positions: opt-in via Settings
        # (positions_retention_days; 0 = keep forever). raw_signals_log (beta capture):
        # its own retention, default 30d, so the full-signal log can't grow unbounded.
        try:
            if time.time() - float(db.get_setting("last_prune_ts", "0") or 0) > 86400:
                ret = int(db.get_setting("positions_retention_days", "0") or 0)
                if ret > 0:
                    db.prune_positions(ret)
                if _research_enabled():
                    db.prune_raw_signals(int(db.get_setting("research_retention_days", "30") or 30))
                db.set_setting("last_prune_ts", str(time.time()))
        except Exception as exc:  # noqa: BLE001
            log.warning("DB prune skipped: %s", exc)

        # Interruptible sleep: while parked we may be sleeping for minutes, so check the
        # boost flag every few seconds and wake immediately if one is requested.
        #
        # Until the EARLIEST car is due, not this car's interval — with a car being driven at 10 s
        # and one parked at 30 s, waiting the parked one's interval would make the driven one miss
        # two polls out of three.
        deadline = min((c.next_due for c in contexts), default=time.time() + 30.0)
        interval = max(0.0, deadline - time.time())
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(5.0, remaining))
            if interval > 10 and any(db.boosting(c.vehicle.vin) for c in contexts):
                break   # a command just landed on one of the cars → go round now

    client.close()
    db.close()


if __name__ == "__main__":
    main()
