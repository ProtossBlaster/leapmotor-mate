"""Per-vehicle capability profile — drives model-aware UI/MQTT (show only what the car
actually supports). Verdict per feature: 'working' | 'broken' | 'untested'.

Two AXES per physical feature (proven on the B10): a SENSOR (the car reports the state)
and a COMMAND (we can actuate it remotely) are INDEPENDENT. The comfort family on the B10
is the archetype: the state sensor works (reflects manual activation) but the remote command
is accepted-but-not-executed. So they are SEPARATE features here — e.g. `seat_heat` (sensor,
shown read-only) vs `seat_heat_cmd` (command/button, hidden when broken).

Verdicts:
- 'working'  : proven — a CORE sensor Mate depends on, or the car reports a live non-default
               value, or an at-the-car probe moved the signal.
- 'broken'   : confirmed empirically — command accepted but never actuates, or a sensor never
               changes despite a real state change. These get HIDDEN.
- 'untested' : unknown right now — shown by default (never hide on a guess).

CORE features are NEVER hidden whatever the verdict — hiding them would break Mate's own
trips/charges/reports/charts (gear, speed, odometer, state, charging, SoC, GPS, doors,
tyres, temps…). A 0 right now means parked/closed/idle, not broken.

`kind`:
- 'sensor'  → a read-only state display / MQTT sensor. Hidden only if confirmed 'broken'.
- 'command' → an actuation button. Hidden if confirmed 'broken'.

Stored per VIN in the settings table as JSON under `capabilities_<vin>` — shared by the web
UI and the poller's MQTT bridge (so this module avoids a hard db_reader import: callers in the
poller pass their own get/set via `accessor=`). Accumulates across sessions.
"""
from __future__ import annotations
import json
from typing import Callable, Optional

# feature_key -> {label, kind, signals(status-signal ids whose live value proves a sensor works),
#                 right(VehicleRight code gating the command, or None), core(bool)}.
# core=True → load-bearing for Mate → always 'working', never hidden.
FEATURES: dict[str, dict] = {
    # --- CORE telemetry: Mate's trips/charges/reports/charts depend on these (proven working) ---
    "soc":           {"label": "Battery SoC",         "kind": "sensor", "signals": ["1204", "100003"], "right": None, "core": True},
    "range":         {"label": "Range",               "kind": "sensor", "signals": ["2188", "3257", "3260"], "right": None, "core": True},
    "odometer":      {"label": "Odometer",            "kind": "sensor", "signals": ["1318"], "right": None, "core": True},
    "state":         {"label": "Vehicle state",       "kind": "sensor", "signals": ["1944"], "right": None, "core": True},
    "gear":          {"label": "Gear",                "kind": "sensor", "signals": ["1010"], "right": None, "core": True},
    "speed":         {"label": "Speed",               "kind": "sensor", "signals": ["1319"], "right": None, "core": True},
    "location":      {"label": "GPS location",        "kind": "sensor", "signals": ["3724", "3725"], "right": None, "core": True},
    "charge_state":  {"label": "Charging state",      "kind": "sensor", "signals": ["1149"], "right": None, "core": True},
    "charge_power":  {"label": "Charge power",        "kind": "sensor", "signals": ["1177", "1178"], "right": None, "core": True},
    "plug":          {"label": "Plug connected",      "kind": "sensor", "signals": ["1149", "1197", "47"], "right": None, "core": True},
    "inside_temp":   {"label": "Inside temperature",  "kind": "sensor", "signals": ["1349"], "right": None, "core": True},
    "battery_temp":  {"label": "Battery temperature", "kind": "sensor", "signals": ["1182"], "right": None, "core": True},
    "tires":         {"label": "Tyre pressures",      "kind": "sensor", "signals": ["2667", "2653", "2646", "2660"], "right": None, "core": True},
    "doors":         {"label": "Door open/closed",    "kind": "sensor", "signals": ["1277", "1278", "1279", "1280", "1281"], "right": None, "core": True},
    "trunk":         {"label": "Trunk",               "kind": "sensor", "signals": ["1281"], "right": 130, "core": True},
    "lock":          {"label": "Lock state",          "kind": "sensor", "signals": ["1298"], "right": 110, "core": True},
    "sunshade":      {"label": "Sunshade / roof",     "kind": "sensor", "signals": ["1724"], "right": 161, "core": True},
    "windows_state": {"label": "Window open/closed",  "kind": "sensor", "signals": ["1693", "1694", "1695", "1696"], "right": 230, "core": True},

    # --- OPTIONAL SENSORS: read-only states, shown unless proven broken on this car ---
    "ac_state":      {"label": "A/C on/off state",    "kind": "sensor", "signals": ["1938"], "right": None, "core": False},
    "ac_target":     {"label": "A/C target temp",     "kind": "sensor", "signals": ["2183"], "right": None, "core": False},
    "seat_heat":     {"label": "Seat heating",        "kind": "sensor", "signals": ["2100", "2118"], "right": None, "core": False},
    "seat_vent":     {"label": "Seat ventilation",    "kind": "sensor", "signals": ["2101", "2119"], "right": None, "core": False},
    "steering_heat": {"label": "Steering wheel heat", "kind": "sensor", "signals": ["1816"], "right": None, "core": False},
    "mirror_heat":   {"label": "Mirror heating",      "kind": "sensor", "signals": ["49", "50"], "right": None, "core": False},
    "windows_pct":   {"label": "Window opening %",    "kind": "sensor", "signals": ["3727", "3728", "1879", "1880"], "right": None, "core": False},

    # --- OPTIONAL COMMANDS: actuation buttons, hidden when proven broken on this car ---
    "climate_off":       {"label": "A/C Off (command)",          "kind": "command", "signals": ["1938"], "right": 170, "core": False},
    "defrost":           {"label": "Windshield defrost",         "kind": "command", "signals": [], "right": 460, "core": False},
    "sentry":            {"label": "Sentry mode (command)",      "kind": "command", "signals": ["3636"], "right": 220, "core": False},
    "seat_heat_cmd":     {"label": "Seat heating (command)",     "kind": "command", "signals": [], "right": 301, "core": False},
    "seat_vent_cmd":     {"label": "Seat ventilation (command)", "kind": "command", "signals": [], "right": 370, "core": False},
    "steering_heat_cmd": {"label": "Steering heat (command)",    "kind": "command", "signals": [], "right": 320, "core": False},
    "mirror_heat_cmd":   {"label": "Mirror heating (command)",   "kind": "command", "signals": [], "right": 440, "core": False},
}

# Map the live MQTT-button / UI-command keys to their gating capability feature, so callers
# can ask "should I expose this button?" by the name they already use. Keys not listed here
# are always shown (no capability gate).
COMMAND_FEATURE = {
    "climate_off":         "climate_off",
    "windshield_defrost":  "defrost",
    "climate_defrost":     "defrost",
    "sentry_on":           "sentry",
    "sentry_off":          "sentry",
    "steering_heat_on":    "steering_heat_cmd",
    "steering_heat_off":   "steering_heat_cmd",
    "mirror_heat_on":      "mirror_heat_cmd",
    "mirror_heat_off":     "mirror_heat_cmd",
    "seat_heat_driver_on":  "seat_heat_cmd",
    "seat_heat_driver_off": "seat_heat_cmd",
    "seat_vent_driver_on":  "seat_vent_cmd",
    "seat_vent_driver_off": "seat_vent_cmd",
    "seat_heat_passenger_on":  "seat_heat_cmd",
    "seat_heat_passenger_off": "seat_heat_cmd",
    "seat_vent_passenger_on":  "seat_vent_cmd",
    "seat_vent_passenger_off": "seat_vent_cmd",
}

# Optional features KNOWN ABSENT on a given model — hardware/right the car simply does not have,
# confirmed from RELIABLE ability evidence (seats + prepare), never a guess. Hidden on that model so
# its owner sees only what actually works. Extend one line per model as new EVs (B05, …) are
# characterised on-car. ⚠️ CLIMATE IS DELIBERATELY EXCLUDED: the T03's climate ability codes are
# misleading (declares CLIMATE_ADVANCED yet ignores some writes; lacks AC_ON yet cools — #67), so
# climate is never gated from this table — only on direct empirical proof, elsewhere.
MODEL_ABSENT: dict[str, tuple[str, ...]] = {
    # T03: no ventilated seats (ability 42/43 absent), no PREPARE right (38) → prepare-car is inert.
    # Also NO heated seats and NO heated steering wheel: confirmed absent on the European T03 (single
    # trim, Tychy-built; the official Stellantis spec lists only heated mirrors) even though the firmware
    # DECLARES SEAT_HEATING / STEERING abilities — a shared-platform over-declaration, the same lie as
    # climate (#67). So they are gated here PER-MODEL, never on the (lying) abilities. #144. The cabin /
    # battery TEMPERATURES stay — they're core sensors; a T03 that doesn't report them shows blank, not
    # a wrong control, and hiding a core sensor on a marketing spec would be the wrong call.
    "T03": ("seat_vent", "seat_vent_cmd", "prepare_car",
            "seat_heat", "seat_heat_cmd", "steering_heat", "steering_heat_cmd"),
}


def model_hidden(car_type: str, feature: str) -> bool:
    """True if `feature` is known-absent on this model → hide it. Pure/stateless (safe for web +
    poller, no db). Unknown model or feature → False (show). Case-insensitive on car_type."""
    return feature in MODEL_ABSENT.get((car_type or "").upper(), ())


def is_core(feature: str) -> bool:
    return bool(FEATURES.get(feature, {}).get("core"))


def kind(feature: str) -> str:
    return FEATURES.get(feature, {}).get("kind", "sensor")


def _is_meaningful(val) -> bool:
    try:
        return float(val) != 0.0
    except (TypeError, ValueError):
        return bool(val)


def _key(vin: str) -> str:
    return f"capabilities_{(vin or '').lower()}"


def _default_get_setting() -> Callable[[str, str], str]:
    import db_reader
    return db_reader.get_setting


def _default_set_setting() -> Callable[[str, str], None]:
    import db_reader
    return db_reader.set_setting


def load(vin: str, get_setting: Optional[Callable] = None) -> dict[str, str]:
    """Load the per-VIN verdict map. `get_setting(key, default)` lets the poller pass its own
    settings accessor (db.get_setting); defaults to web's db_reader."""
    try:
        get_setting = get_setting or _default_get_setting()
        raw = get_setting(_key(vin), "")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def save(vin: str, profile: dict[str, str], set_setting: Optional[Callable] = None) -> None:
    set_setting = set_setting or _default_set_setting()
    set_setting(_key(vin), json.dumps(profile, separators=(",", ":")))


def set_verdict(vin: str, feature: str, verdict: str,
                get_setting: Optional[Callable] = None, set_setting: Optional[Callable] = None) -> None:
    """Record an empirical result (working/broken/untested) for one feature."""
    assert verdict in ("working", "broken", "untested")
    p = load(vin, get_setting)
    p[feature] = verdict
    save(vin, p, set_setting)


def verdict(vin: str, feature: str, default: str = "untested",
            get_setting: Optional[Callable] = None) -> str:
    if is_core(feature):
        return "working"
    return load(vin, get_setting).get(feature, default)


def is_shown(vin: str, feature: str, get_setting: Optional[Callable] = None,
             *, car_type: str = "") -> bool:
    """Show everything EXCEPT what is confirmed 'broken' or known-absent on this model. CORE
    features are NEVER hidden (they power Mate's own reports/charts). Unknown/'untested' features
    are shown — we never hide on a guess. Pass `car_type` to enable per-model hiding via
    MODEL_ABSENT; omit it and behaviour is byte-for-byte unchanged (so callers that don't know the
    model, and every non-listed model, are unaffected)."""
    if is_core(feature):
        return True
    if car_type and model_hidden(car_type, feature):
        return False
    return load(vin, get_setting).get(feature, "untested") != "broken"


# Window open/closed mapping, shared by every surface so they always agree (#62). The open/closed
# flags (1693-1696) are live on the B10 but DEAD on the T03 (stay 0 even when open); the position %
# (3727/3728/1879/1880) is the opposite — live on the T03, absent on the B10. A window is OPEN if
# its flag is set OR (where the % is consulted) its position is > 0. Pairs: FL 1693<->3727,
# FR 1694<->3728, RL 1695<->1879, RR 1696<->1880.
_WINDOW_PAIRS = (("1693", "3727"), ("1694", "3728"), ("1695", "1879"), ("1696", "1880"))


def window_open_states(signals: dict, use_pct: bool) -> list:
    """Per-window open state [FL, FR, RL, RR]: True / False, or None when the car reports neither
    the flag nor the position for that window. `use_pct` gates the position-% fallback — the caller
    decides it (web: is_shown(vin, 'windows_pct'); poller: bool(vin)) so a car whose % sensor is
    untrusted never false-positives. The B10 is safe because it doesn't emit the % signals at all."""
    def _i(k):
        v = signals.get(k)
        try:
            return int(float(v)) if v is not None else None
        except (TypeError, ValueError):
            return None
    states = []
    for state_k, pct_k in _WINDOW_PAIRS:
        s = _i(state_k)
        p = _i(pct_k) if use_pct else None
        # Open when the position % says so (0 % = shut) OR when the coarse status flag is non-zero.
        # The B10 reports the flag as 2 when OPEN and 0 when shut (verified on-car against the official
        # app: flag 2 = open). Its % sensor is dead (always 0), so the flag is the only truth there;
        # the T03 is the opposite (flag dead at 0, the % is live). A stale cloud frame can momentarily
        # serve an old value, but that's a connectivity artifact, not the flag's meaning. (Reverts the
        # over-narrow `flag == 1` from #68, which mis-read a stale "2" frame as shut.)
        states.append(None if (s is None and p is None) else bool((p or 0) > 0 or (s or 0) != 0))
    return states


# Commands gated on a DECLARED vehicle ABILITY — the code the car itself lists in its abilities
# payload (VehicleAbility; the "what the car declares it can do" set shown in the diagnostics
# bundle). Unlike MODEL_ABSENT this needs NO per-model table: any car that doesn't declare the code
# hides the button, present and future models alike — the car is the source of truth about itself.
# WHITELIST ONLY, and only where the declaration is proven RELIABLE. `unlock_charger` qualifies on
# three concordant signals: the T03 omits code 53, the official app hides the option, and the button
# no-ops there (#142). ⚠️ Climate is deliberately NOT gated this way — the T03 omits AC_ON (6) yet
# cools, so its declarations lie there (#67); gating A/C on the ability would wrongly hide it.
COMMAND_ABILITY = {
    "unlock_charger": 53,   # VehicleAbility.UNLOCK_CHARGE_GUN
}


def parse_abilities(raw) -> Optional[list]:
    """Normalise a stored abilities value (JSON string from vehicles.abilities, or a list) to a list
    of int codes, or None when absent/unparseable → callers treat None as 'not declared → show'."""
    if not raw:
        return None
    try:
        seq = json.loads(raw) if isinstance(raw, str) else raw
        return [int(a) for a in seq]
    except (ValueError, TypeError):
        return None


def ability_supported(command_key: str, abilities) -> bool:
    """False ONLY when the car has declared its abilities and the code this command needs is absent.
    `abilities` = the parsed list/set of declared VehicleAbility codes, or None if the car hasn't
    reported them yet (→ True; never hide on a guess). Commands not in COMMAND_ABILITY → True."""
    code = COMMAND_ABILITY.get(command_key)
    if code is None or abilities is None:
        return True
    try:
        return code in {int(a) for a in abilities}
    except (TypeError, ValueError):
        return True


def command_shown(vin: str, command_key: str, get_setting: Optional[Callable] = None,
                  *, abilities=None, car_type: str = "") -> bool:
    """Should a UI/MQTT command button named `command_key` be exposed? Hidden when the car declared
    its abilities and doesn't list the one this command needs (COMMAND_ABILITY), or when its gating
    feature is known-absent on this model (`car_type` → MODEL_ABSENT, e.g. heated-seat commands on a
    T03 — #144); otherwise mapped to its gating feature (COMMAND_FEATURE), and commands with neither
    are always shown."""
    if not ability_supported(command_key, abilities):
        return False
    feat = COMMAND_FEATURE.get(command_key)
    return True if feat is None else is_shown(vin, feat, get_setting, car_type=car_type)


# Feature-level ability gate (not a command button): the WEEKLY/CYCLIC charge schedule. A car that
# declares none of these codes only offers a simple start-time + target-SoC plan, so the Scheduling
# view drops the end-time window and the day-of-week chips for it (the T03 declares CHARGE_LIMIT but
# none of these — #146). Model-blind like COMMAND_ABILITY: the car's own declaration is the source
# of truth, so any simple-scheduler model (present or future) is covered with no per-model table.
CHARGE_SCHEDULE_WEEKLY_ABILITY = {
    25,   # CYCLIC_CHARGE
    26,   # CHARGE_REPEAT_WEEKLY
    47,   # CYCLIC_CHARGE_TRIGGER
    51,   # WEEKLY_CHARGE_REPEAT
}


def charge_schedule_advanced(abilities) -> bool:
    """True if the car supports the full weekly charge schedule (end-time window + day-of-week chips).
    False ONLY when it declared its abilities and lists none of the weekly/cyclic codes (e.g. T03).
    Abilities unknown (None) → True: never hide a control on a guess (mirrors ability_supported)."""
    if abilities is None:
        return True
    try:
        return bool(CHARGE_SCHEDULE_WEEKLY_ABILITY & {int(a) for a in abilities})
    except (TypeError, ValueError):
        return True


def seed_from_signals(vin: str, signals: dict, *, overwrite: bool = False,
                      get_setting: Optional[Callable] = None, set_setting: Optional[Callable] = None) -> dict[str, str]:
    """Read-only seed. CORE features → 'working' (proven by Mate's existing functionality,
    regardless of the instantaneous value). Non-core → 'working' if the car reports a live
    non-default value, else left 'untested' (a 0 may just mean off/closed — never 'broken').
    Existing 'broken'/'working' verdicts are preserved unless overwrite=True."""
    p = {} if overwrite else load(vin, get_setting)
    for feat, meta in FEATURES.items():
        if meta.get("core"):
            p[feat] = "working"
            continue
        if not overwrite and p.get(feat) in ("working", "broken"):
            continue
        if meta["signals"] and any(_is_meaningful(signals.get(sid)) for sid in meta["signals"]):
            p[feat] = "working"
        else:
            p.setdefault(feat, "untested")
    save(vin, p, set_setting)
    return p
