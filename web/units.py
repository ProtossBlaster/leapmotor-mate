"""Display-time unit conversion.

Every value Mate stores is METRIC (km, km/h, °C, bar) — the Leapmotor cloud always reports
metric (verified: the kerniger HA integration declares every sensor metric with no conversion;
CAN telemetry is fixed-unit; the API's `language` param only sets HTTP headers, never scales a
signal). These helpers convert ONLY for display, per the user's `unit_system` setting. Nothing
in the DB or the poller is touched — switch the setting and everything re-displays, no migration.

unit_system: 'metric' | 'imperial_uk' | 'imperial_us'
  distance/speed: metric = km / km/h ; both imperial = mi / mph
  temperature:    imperial_us = °F ; metric AND imperial_uk = °C   (the UK keeps Celsius)
  pressure:       metric = bar ; both imperial = psi
  efficiency:     metric = kWh/100km ; both imperial = mi/kWh
"""
import db_reader

_KM_TO_MI = 0.621371
_BAR_TO_PSI = 14.5037738
_M_TO_FT = 3.28084

UNIT_SYSTEMS = ("metric", "imperial_uk", "imperial_us")


def get_unit_system() -> str:
    s = db_reader.get_setting("unit_system", "metric")
    return s if s in UNIT_SYSTEMS else "metric"


def _imperial(system: str) -> bool:
    return system in ("imperial_uk", "imperial_us")


def decimal_point(s: str) -> str:
    """Put the decimal separator in the UI language's own place.

    `money` and `price3` have always done this, and nothing else did: in Italian the Monthly
    Report showed a cost of "38,74 €" beside an energy of "110.3 kWh" — the same page writing
    the same kind of number two ways. Applied here and in `main._nice`, which between them
    format every displayed number, so the whole app now follows one rule.

    Only the decimal mark: no thousands grouping, because these are short quantities (kWh, km,
    °C) where a group separator would be noise, and `money` already handles the amounts where
    it isn't. English keeps the dot.

    Formatting a number must never be able to raise: with no database reachable (a unit test on
    the pure conversion helpers, a very early request) the dot stands."""
    try:
        return s.replace(".", ",") if db_reader.get_language() != "en" else s
    except Exception:                                            # noqa: BLE001
        return s


def _num(v: float, dec: int) -> str:
    """Number with `dec` decimals, trailing zeros trimmed (matches main._nice)."""
    s = f"{float(v):.{dec}f}"
    return decimal_point(s.rstrip("0").rstrip(".") if dec else s)


# ── unit labels (for chart axes / headers: "Distance ({{ dist_unit() }})") ────
def dist_unit(system=None) -> str:
    return "mi" if _imperial(system or get_unit_system()) else "km"

def speed_unit(system=None) -> str:
    return "mph" if _imperial(system or get_unit_system()) else "km/h"

def temp_unit(system=None) -> str:
    return "°F" if (system or get_unit_system()) == "imperial_us" else "°C"

def pressure_unit(system=None) -> str:
    return "psi" if _imperial(system or get_unit_system()) else "bar"

def eff_unit(system=None) -> str:
    return "mi/kWh" if _imperial(system or get_unit_system()) else "kWh/100km"

def elev_unit(system=None) -> str:
    return "ft" if _imperial(system or get_unit_system()) else "m"

def dist100_unit(system=None) -> str:
    """The denominator of a running-cost figure: "100 km", or "100 mi" for a reader whose
    efficiency card already says mi/kWh."""
    return "100 mi" if _imperial(system or get_unit_system()) else "100 km"


# ── converted numbers only (for JS chart data / attributes) ──────────────────
def dist_val(km, dec=1, system=None):
    if km is None:
        return None
    return round(km * _KM_TO_MI, dec) if _imperial(system or get_unit_system()) else round(km, dec)

def cost100_val(cost_per_100km, system=None):
    """A cost-per-100km figure in the reader's own distance unit — left to `money` to format, so
    the currency's own decimals and separators still decide how it is written.

    ⚠️ The number GROWS on imperial, it does not shrink: 100 miles is 160.9 km, so covering them
    costs more, not less. This is the reciprocal of `dist_val`'s direction and the reason it is a
    function of its own instead of a distance conversion someone reuses by mistake."""
    if cost_per_100km is None:
        return None
    return cost_per_100km / _KM_TO_MI if _imperial(system or get_unit_system()) else cost_per_100km

def dist_to_km(value, system=None):
    """Inverse of dist_val: a distance the user TYPED in their unit (mi for imperial)
    → km for storage. DB is always metric, so user-entered odometer/service km must be
    converted back before saving."""
    if value is None:
        return None
    return value / _KM_TO_MI if _imperial(system or get_unit_system()) else value


def speed_val(kmh, dec=0, system=None):
    if kmh is None:
        return None
    return round(kmh * _KM_TO_MI, dec) if _imperial(system or get_unit_system()) else round(kmh, dec)

def temp_val(c, dec=0, system=None):
    if c is None:
        return None
    return round(c * 9 / 5 + 32, dec) if (system or get_unit_system()) == "imperial_us" else round(c, dec)

def elev_val(m, dec=0, system=None):
    if m is None:
        return None
    return round(m * _M_TO_FT, dec) if _imperial(system or get_unit_system()) else round(m, dec)

def eff_val(kwh_100km, dec=1, system=None):
    """Converted efficiency number only (for chart data). NB: imperial mi/kWh is the RECIPROCAL of
    metric kWh/100km, so a chart switches sense (higher = better) when imperial — pair with eff_unit()."""
    if not kwh_100km:
        return None
    s = system or get_unit_system()
    return round(_KM_TO_MI * 100 / kwh_100km, dec) if _imperial(s) else round(kwh_100km, dec)


# ── formatted "<value> <unit>" filters (the common case) ─────────────────────
def dist(km, dec=1):
    if km is None:
        return "—"
    s = get_unit_system()
    return f"{_num(km * _KM_TO_MI, dec)} mi" if _imperial(s) else f"{_num(km, dec)} km"

def speed(kmh, dec=0):
    if kmh is None:
        return "—"
    s = get_unit_system()
    return f"{_num(kmh * _KM_TO_MI, dec)} mph" if _imperial(s) else f"{_num(kmh, dec)} km/h"

def temp(c, dec=0):
    if c is None:
        return "—"
    if get_unit_system() == "imperial_us":
        return f"{_num(c * 9 / 5 + 32, dec)} °F"
    return f"{_num(c, dec)} °C"

def pressure(bar):
    if bar is None:
        return "—"
    s = get_unit_system()
    return f"{_num(bar * _BAR_TO_PSI, 0)} psi" if _imperial(s) else f"{_num(bar, 2)} bar"

def elev(m, dec=0):
    if m is None:
        return "—"
    s = get_unit_system()
    return f"{_num(m * _M_TO_FT, dec)} ft" if _imperial(s) else f"{_num(m, dec)} m"

def efficiency(kwh_100km, dec=1):
    """kWh/100km (metric) ↔ mi/kWh (imperial). 0/None → em dash."""
    if not kwh_100km:
        return "—"
    s = get_unit_system()
    return f"{_num(_KM_TO_MI * 100 / kwh_100km, dec)} mi/kWh" if _imperial(s) else f"{_num(kwh_100km, dec)} kWh/100km"
