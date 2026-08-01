"""Ready-triggered "prepare now" automation (design agreed with Silvio 2026-07-02): the moment the
car goes READY (ignition/power-on, signal 1258), fire a one-shot climate/seats/windows preparation
— the same action as manually tapping "Prepara Ora", but automatic. Optionally gated on the
interior temperature (e.g. only pre-cool above 25°C, or only pre-heat below 5°C — no external-temp
signal exists in the cloud API, so the condition is necessarily interior-only).

Edge-triggered, NOT repeated: fires ONCE per Ready session (Ready OFF→ON), never re-fires while
still Ready even if the temperature condition changes truth value mid-session, and does not
re-fire for a later trip within the same session — a Ready session can span several Mate trips
(see db_reader.ready_session(): "the cloud's SESSION runs from READY-on until the car is switched
OFF, so it can span SEVERAL Mate trips + idle"). Two robustness rules, both lessons already learned
on THIS exact signal elsewhere in the codebase:
  - Debounce: `ready` occasionally blips OFF for a single poll (db_reader._READY_DEBOUNCE_S: "brief
    ready=0 dips shorter than this — signal blips seen in the log"). Re-arming on the very first 0
    seen would let a blip double-fire the action mid-session; only re-arm after `ready` has read 0
    continuously for READY_DEBOUNCE_S.
  - Restart safety: a poller restart mid-session must not re-fire the action just because this
    module's own "previous ready" memory was lost — the first poll after (re)start only seeds
    state, it never fires (mirrors Recorder._resume_or_close's reasoning for trip/charge sessions).

Commands go through the POLLER's own already-authenticated session (client._api / client._vehicle,
under the same api_lock the MQTT command bridge uses) — no second login, no session eviction risk.
`build_prepare_bundle` is imported from web/command_client (a pure, session-free function — the
exact same builder "Prepara Ora" uses) so the two paths can never drift into different payloads.
"""
import json
import logging
import time

log = logging.getLogger("leapmotor.ready_automation")

READY_DEBOUNCE_S = 60   # same job and same number as db_reader._READY_BLIP_S: how short a
                        # ready=0 gap has to be to be a glitch rather than a real switch-off

_VALID_PRESETS = {"cool", "heat", "vent", "defrost", "none"}
_VALID_SEAT_MODES = {"off", "heat", "vent"}

_last_ready: bool | None = None   # None = unknown (startup/restart) | True | False
_fired = False                    # already actuated for the CURRENT on-session
_off_since: float | None = None   # monotonic ts of the first continuous ready=0 poll


def _load_config(db) -> dict:
    """Sanitised config from the `ready_automation` setting (JSON blob). Defensive against a
    hand-edited or partial DB value — never raises, always returns safe defaults."""
    try:
        raw = json.loads(db.get_setting("ready_automation", "") or "{}")
        if not isinstance(raw, dict):
            raw = {}
    except (ValueError, TypeError):
        raw = {}
    ac_preset = raw.get("ac_preset")
    if ac_preset not in _VALID_PRESETS:
        ac_preset = None
    try:
        ac_temperature = float(raw.get("ac_temperature"))
    except (TypeError, ValueError):
        ac_temperature = 22.0
    ac_temperature = max(18.0, min(ac_temperature, 32.0))
    windows_pct = raw.get("windows_pct")
    try:
        windows_pct = None if windows_pct is None else max(0, min(int(windows_pct), 100))
    except (TypeError, ValueError):
        windows_pct = None

    def _seat(key):
        v = raw.get(key)
        return v if v in _VALID_SEAT_MODES else "off"

    try:
        temp_value = float(raw.get("temp_value"))
    except (TypeError, ValueError):
        temp_value = 25.0
    return {
        "enabled":         bool(raw.get("enabled")),
        "temp_enabled":    bool(raw.get("temp_enabled")),
        "temp_comparator": raw.get("temp_comparator") if raw.get("temp_comparator") in (">", "<") else ">",
        "temp_value":      temp_value,
        "ac_preset":       ac_preset,
        "ac_temperature":  ac_temperature,
        "windows_pct":     windows_pct,
        "seat_driver":     _seat("seat_driver"),   # per-seat, matches Prepara Ora's own two
        "seat_copilot":    _seat("seat_copilot"),  # independent driver/passenger selectors
        "steering":        bool(raw.get("steering")),
        "mirror":          bool(raw.get("mirror")),
    }


def _condition_met(cfg: dict, inside_temp: float) -> bool:
    if not cfg["temp_enabled"]:
        return True
    if cfg["temp_comparator"] == ">":
        return inside_temp > cfg["temp_value"]
    return inside_temp < cfg["temp_value"]


def _ensure_web_on_path() -> None:
    """Same cross-directory trick Recorder._read_wallbox_energy uses for ha_client — web/ isn't
    normally on the poller's sys.path, but command_client.py's own imports are lightweight
    (leapmotor_api + stdlib, no fastapi), so it's safe to reach into from here."""
    import sys
    import pathlib
    web = str(pathlib.Path(__file__).resolve().parent.parent / "web")
    if web not in sys.path:
        sys.path.insert(0, web)


def _build_bundle(cfg: dict) -> dict:
    _ensure_web_on_path()
    from command_client import build_prepare_bundle   # pure function, no session state
    seats = None
    if cfg["seat_driver"] != "off" or cfg["seat_copilot"] != "off":
        seats = {"driver": cfg["seat_driver"], "copilot": cfg["seat_copilot"]}
    return build_prepare_bundle(
        ac_preset=cfg["ac_preset"], ac_temperature=cfg["ac_temperature"],
        seats=seats, steering=cfg["steering"], mirror=cfg["mirror"],
    )


def _windows_native(pct: int, car_type: str) -> str:
    _ensure_web_on_path()
    from command_client import _WINDOWS_SCALE   # same native-scale table the web side uses —
    full = _WINDOWS_SCALE.get((car_type or "").upper(), 100)   # imported, not duplicated: this
    return str(round(max(0, min(pct, 100)) / 100 * full))      # table gets corrected per-model


def maybe_trigger(db, client, data, api_lock, now: float = None) -> bool:
    """Per-poll hook: fire the automation on a Ready OFF→ON edge if enabled and the optional
    temperature condition is met. Best-effort — never raises, a failure can't disturb the poll.
    Returns True when a command was actually sent."""
    global _last_ready, _fired, _off_since
    now = time.monotonic() if now is None else now
    ready = bool(getattr(data, "ready", False))
    try:
        if _last_ready is None:
            _last_ready = ready       # first poll after (re)start: seed only, never fire
            return False

        if not ready:
            if _off_since is None:
                _off_since = now
            if now - _off_since >= READY_DEBOUNCE_S:
                _fired = False        # confirmed off long enough — re-arm for the next session
            _last_ready = False
            return False
        _off_since = None             # any ready=1 poll clears a pending debounce window

        rising_edge = not _last_ready
        _last_ready = True
        if not rising_edge or _fired:
            return False
        _fired = True                 # a real Ready-on happened — never re-evaluate mid-session,
                                       # even if the temperature crosses the threshold later on

        cfg = _load_config(db)
        if not cfg["enabled"] or not _condition_met(cfg, getattr(data, "inside_temp", 0.0)):
            return False

        bundle = _build_bundle(cfg)
        sent = False
        with api_lock:
            if bundle:
                client._api.prepare_car(client._vehicle.vin, params=bundle)
                sent = True
            if cfg["windows_pct"] is not None:
                client._api.windows(client._vehicle.vin,
                                    value=_windows_native(cfg["windows_pct"], client._vehicle.car_type))
                sent = True
        if sent:
            log.info("Ready automation fired: bundle=%s windows_pct=%s (inside_temp=%.1f)",
                     list(bundle.keys()), cfg["windows_pct"], getattr(data, "inside_temp", None))
        return sent
    except Exception as e:  # noqa: BLE001
        log.warning("Ready automation failed: %s", e)
        return False
