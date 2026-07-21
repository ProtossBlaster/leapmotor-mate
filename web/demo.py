"""MATE_DEMO mode — run Mate on the bundled sample database with no account or
cloud, while keeping the UI interactive: remote commands mutate the demo's own
state (via the web's existing optimistic overlay) instead of reaching a real car.

Activated by the MATE_DEMO env var (set by run.sh). Everything here is a no-op
unless that var is truthy, so a normal install is completely unaffected.
"""
import os


def is_demo() -> bool:
    return os.environ.get("MATE_DEMO", "") not in ("", "0", "false", "False", "no")


def flag_path() -> str:
    """Marker that lets a user enter/leave demo from INSIDE Mate (the setup screen's
    "Try the demo" button and the in-demo exit banner) — no command line, no add-on
    config. It lives next to the DB, i.e. on the persistent /data volume both as an HA
    add-on and standalone, so it survives the restart. run.sh reads the SAME path at boot
    to decide whether to start in demo (see run.sh)."""
    db = os.environ.get("DB_PATH", "/data/leapmotor_mate.db")
    return os.path.join(os.path.dirname(os.path.abspath(db)), "demo.flag")


def set_flag(on: bool) -> None:
    """Persist the in-app demo toggle. Takes effect on the NEXT container start — the
    caller is responsible for restarting the container so run.sh re-reads it."""
    p = flag_path()
    if on:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("1\n")
    else:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


# Synthetic wallbox readings so the Wallbox page renders fully in the demo (the real
# session history still comes from the sample charges in the DB).
_WB_LIVE = {"configured": True, "power_kw": 7.2, "energy_kwh": 9.6, "status": "Charging",
            "max_current_a": 16, "charging": True, "speed": 38, "speed_unit": "km/h",
            "max_power": 7.4, "max_power_unit": "kW"}
_WB_MAX_CURRENT = {"value": 16, "min": 6, "max": 32, "step": 1, "unit": "A"}
_WB_MAPPING = {"power": "sensor.demo_wallbox_power", "energy": "sensor.demo_wallbox_energy",
               "max_current": "number.demo_wallbox_max_current"}


def install(command_client, ha_client=None) -> None:
    """Neuter every cloud / Home-Assistant path so no demo action hits the network or
    errors, and feed the Wallbox page realistic synthetic readings. The visible effect of
    a command is applied by the web layer (run_command reuses its optimistic overlay)."""
    if not is_demo():
        return
    ok = (True, "demo")
    try:
        command_client._session.execute = lambda *a, **k: ok   # covers lock/unlock/ac/windows/…
    except Exception:  # noqa: BLE001
        pass
    command_client.get_fresh_signals = lambda *a, **k: None     # refresh/debug re-render from DB
    for fn in ("seat_comfort", "set_climate_temp", "send_destination"):
        if hasattr(command_client, fn):
            try:
                setattr(command_client, fn, lambda *a, **k: ok)
            except Exception:  # noqa: BLE001
                pass
    if ha_client is not None:
        try:
            ha_client.is_configured = lambda *a, **k: True
            ha_client.get_mapping = lambda *a, **k: dict(_WB_MAPPING)
            ha_client.get_live = lambda *a, **k: dict(_WB_LIVE)
            ha_client.get_max_current_config = lambda *a, **k: dict(_WB_MAX_CURRENT)
            ha_client.set_max_current = lambda *a, **k: True
            ha_client.get_state = lambda *a, **k: None
            ha_client.test_connection = lambda *a, **k: {"ok": True, "message": "demo"}
        except Exception:  # noqa: BLE001
            pass


def vehicle_status(db_reader) -> dict:
    """Build the /vehicle page's status (tyres/doors/windows/temps) from the demo DB,
    since there are no live cloud signals in demo mode."""
    s = db_reader.get_latest_status() or {}

    def ob(v):
        return None if v is None else bool(v)

    return {
        "tyres": {"fl": {"bar": 2.4, "low": False}, "fr": {"bar": 2.4, "low": False},
                  "rl": {"bar": 2.3, "low": False}, "rr": {"bar": 2.3, "low": False}},
        "doors": {"driver": False, "passenger": False, "rear_left": False,
                  "rear_right": False, "trunk": ob(s.get("trunk_open"))},
        "windows": {"fl": ob(s.get("windows_open")), "fr": False, "rl": False, "rr": False,
                    "sunshade": False},
        "temps": {"battery": s.get("battery_min_temp"), "cabin": s.get("inside_temp")},
        # Same shape as _parse_vehicle_status' climate block. Added late: the climate card
        # arrived on the vehicle page after this stub was written, and the template's
        # `vs.climate` then raised on every demo visit — a 500 on the one page a first-time
        # visitor is most likely to poke at. The demo DB has no climate signals, so the values
        # come from the position row where one exists and read as "unknown" otherwise.
        "climate": {
            "on": ob(s.get("climate_on")),
            "mode": None,
            "fan": None,
            "recirc": None,
            "target": None,
        },
    }
