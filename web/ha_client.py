"""Home Assistant REST client — used to read wallbox entities into Mate.

Dual auth, no config needed in the common case:
  • Add-on:     uses the Supervisor token (SUPERVISOR_TOKEN env) → talks to
                http://supervisor/core/api, the in-cluster HA Core proxy.
  • Standalone: uses a Long-Lived Access Token + base URL saved in Settings.

Stdlib only (urllib) to avoid adding a dependency, same style as the ABRP
forwarder. All calls are best-effort and never raise to the caller: they
return a structured dict so the web layer can render a friendly result.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import quote

import db_reader

_TIMEOUT = 8  # seconds — local network, keep snappy

# Entities likely to belong to a wallbox / EV charger, by name or device_class.
_WB_KEYWORDS = (
    "wallbox", "charger", "charging", "evse", "easee", "go-e", "goe",
    "keba", "wallbe", "zaptec", "openevse", "tesla_wall", "pulsar",
    # FR/other-language charger terms (issue #21 — Feyree relay via Tuya Local)
    "borne", "recharge", "feyree", "borne_recharge",
    # V2C Trydan (issue #44 — entities are sensor.evse_v2c_trydan_local_*)
    "v2c", "trydan",
)
_WB_DEVICE_CLASSES = ("power", "energy", "current", "voltage")


def _parse_wb_keywords() -> tuple:
    """The user's wb_keywords setting parsed into lowercased tokens. The single
    source of truth for both helpers below — delimiter/whitespace-only input
    (e.g. ",") collapses to an empty tuple, so it cleanly falls back to defaults."""
    raw = db_reader.get_setting("wb_keywords", "")
    return tuple(k.strip().lower() for k in raw.split(",") if k.strip())


def get_wallbox_keywords() -> tuple:
    """Wallbox detection keywords: the user's custom list, or the defaults when unset."""
    return _parse_wb_keywords() or _WB_KEYWORDS


def is_custom_keywords() -> bool:
    """True when the user has a non-empty custom keyword list (so detection matches
    only those, with no device_class fallback). Agrees with get_wallbox_keywords()."""
    return bool(_parse_wb_keywords())


def _creds() -> tuple[str | None, str | None]:
    """Return (base_url, token). Supervisor token wins when present (add-on)."""
    sup = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
    if sup:
        return "http://supervisor/core", sup
    base = db_reader.get_setting("ha_url", "").strip().rstrip("/")
    token = db_reader.get_secret("ha_token", "").strip()
    if base and token:
        return base, token
    return None, None


def is_configured() -> bool:
    base, token = _creds()
    return bool(base and token)


def _request(path: str, method: str = "GET", payload: object | None = None) -> tuple[int, object]:
    """base+path with bearer auth. Returns (status, parsed_json_or_text)."""
    base, token = _creds()
    if not base or not token:
        raise RuntimeError("not_configured")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    # Local HA over https often uses a self-signed cert → don't hard-fail on it.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as resp:
        raw = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw


def test_connection() -> dict:
    """Ping HA's API root. {ok: bool, message|error: str}."""
    if not is_configured():
        return {"ok": False, "error": "not_configured"}
    try:
        status, body = _request("/api/")
        if status == 200:
            msg = body.get("message") if isinstance(body, dict) else str(body)
            return {"ok": True, "message": msg or "API running"}
        return {"ok": False, "error": f"HTTP {status}"}
    except urllib.error.HTTPError as e:
        # 401 = bad/expired token, the most common real-world failure
        return {"ok": False, "error": f"HTTP {e.code} ({'unauthorized — check the token' if e.code == 401 else e.reason})"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"unreachable — {e.reason} (check the URL)"}
    except Exception as e:  # noqa: BLE001 — never bubble up to the UI
        return {"ok": False, "error": str(e)}


def _fetch_states() -> list[dict]:
    """Raw /api/states body, or [] on any failure/misconfiguration. Shared by every
    entity-discovery helper (wallbox, dynamic price, …) so each is a pure filter over
    one bulk fetch instead of its own HTTP round-trip."""
    if not is_configured():
        return []
    try:
        status, body = _request("/api/states")
    except Exception:  # noqa: BLE001
        return []
    return body if status == 200 and isinstance(body, list) else []


def list_entities(only_wallbox: bool = True) -> list[dict]:
    """Return entities as {entity_id, name, state, unit, device_class}.

    When only_wallbox, keep entities whose id/name matches a charger keyword
    or whose device_class is power/energy/current/voltage. Name matches are
    ranked first so the user's actual wallbox surfaces at the top of the list.
    """
    body = _fetch_states()
    out: list[dict] = []
    custom_kw = _parse_wb_keywords()          # one settings read for the whole scan
    keywords = custom_kw or _WB_KEYWORDS
    for st in body:
        eid = st.get("entity_id", "")
        attrs = st.get("attributes", {}) or {}
        name = attrs.get("friendly_name") or eid
        dclass = attrs.get("device_class", "")
        unit = attrs.get("unit_of_measurement", "")
        hay = f"{eid} {name}".lower()
        kw_match = any(k in hay for k in keywords)
        # Custom keywords replace detection entirely (match only those); the
        # default list keeps the power/energy/current/voltage device_class fallback.
        cls_match = (not custom_kw) and dclass in _WB_DEVICE_CLASSES
        if only_wallbox and not (kw_match or cls_match):
            continue
        out.append({
            "entity_id": eid,
            "name": name,
            "state": st.get("state", ""),
            "unit": unit,
            "device_class": dclass,
            "_rank": (0 if kw_match else 1, dclass or "zzz", eid),
        })
    out.sort(key=lambda e: e.pop("_rank"))
    return out


# Entities likely to be a dynamic/spot electricity price (Nordpool, Tibber, ENTSO-E, a
# utility's own integration, or a manual input_number some users drive from an automation).
_PRICE_KEYWORDS = ("price", "prezzo", "tariff", "tariffa", "nordpool", "tibber", "entsoe",
                   "spot", "energy_price", "elpris", "strompreis")
_PRICE_UNITS = {"eur/kwh", "€/kwh", "ct/kwh", "cent/kwh", "usd/kwh", "gbp/kwh", "öre/kwh"}


def list_price_entities() -> list[dict]:
    """Candidate electricity-price entities for the dynamic pricing mode: sensor/input_number
    entities whose id/name matches a price keyword, or whose unit is a per-kWh currency (the
    strongest, language-independent signal — a Nordpool/Tibber/ENTSO-E sensor always reports one).
    Keyword matches are ranked first, same convention as list_entities()."""
    body = _fetch_states()
    out: list[dict] = []
    for st in body:
        eid = st.get("entity_id", "")
        domain = eid.split(".", 1)[0]
        if domain not in ("sensor", "input_number", "number"):
            continue
        attrs = st.get("attributes", {}) or {}
        name = attrs.get("friendly_name") or eid
        unit = attrs.get("unit_of_measurement", "")
        hay = f"{eid} {name}".lower()
        kw_match = any(k in hay for k in _PRICE_KEYWORDS)
        unit_match = unit.strip().lower() in _PRICE_UNITS
        if not (kw_match or unit_match):
            continue
        out.append({
            "entity_id": eid, "name": name, "state": st.get("state", ""), "unit": unit,
            "_rank": (0 if kw_match else 1, eid),
        })
    out.sort(key=lambda e: e.pop("_rank"))
    return out


def get_state(entity_id: str) -> dict | None:
    """Current state of one entity, or None on any failure."""
    if not entity_id or not is_configured():
        return None
    try:
        status, body = _request(f"/api/states/{entity_id}")
        return body if status == 200 and isinstance(body, dict) else None
    except Exception:  # noqa: BLE001
        return None


# ── Wallbox entity roles ──────────────────────────────────────────────────────
# Each role maps to one HA entity. The picker lets the user choose; we pre-select
# sensible defaults by scoring discovered entities against these hints.
WB_ROLES = ("power", "energy", "status", "max_current", "speed", "max_power")

_ROLE_HINTS = {
    # role:        (domains,        positive keywords,                              device_class)
    "power":       (("sensor",),    ("potenza_di_carica", "charging_power", "power"), "power"),
    "energy":      (("sensor",),    ("energia_di_carica", "energia_aggiunta", "added_grid_energy", "added_energy", "charge_energy", "energy"), "energy"),
    "status":      (("sensor",),    ("descrizione_dello_stato", "status", "stato", "state"), None),
    "max_current": (("number",),    ("corrente_di_carica_massima", "max_charging_current", "massima"), None),
    "speed":       (("sensor",),    ("velocita_di_carica", "charging_speed", "charge_speed"), None),
    "max_power":   (("sensor",),    ("potenza_massima_disponibile", "max_available_power", "available_power", "disponibile"), None),
}
# Names that almost always mean "not the wallbox's own metric" → penalise. Solar/house-power
# sensors (e.g. V2C's "Energia fotovoltaica" / "Alimentazione domestica Inverted") are
# device_class power too, so they must be kept out of the charging-power/energy roles (#44).
_NEG = ("leapmotor", "differenza", "ups", "green", "scaricata", "deposito", "prezzo", "costo",
        "filtered", "fotovoltaica", "fotovoltaico", "photovoltaic", "solar", "domestica", "inverted")


def _score(entity: dict, domains, keywords, dclass) -> int:
    eid = entity["entity_id"]
    domain = eid.split(".", 1)[0]
    if domain not in domains:
        return -999
    hay = f"{eid} {entity['name']}".lower()
    s = 0
    if "wallbox" in hay or "pulsar" in hay:
        s += 3
    for i, kw in enumerate(keywords):           # earlier keyword = stronger
        if kw in hay:
            s += 5 - i
            break
    if dclass and entity.get("device_class") == dclass:
        s += 2
    if any(n in hay for n in _NEG):
        s -= 4
    return s


# Units a role's sensor may carry. The picker offers only matching entities so a kWh energy
# sensor can't be mapped to the kW power role, or vice-versa — the one swap that corrupts the
# stored power/cost data the DB derives. Lower-cased for a case-insensitive match.
# ONLY `power` and `energy` are narrowed, on purpose: they are the DB-critical pair AND the one
# confusable by unit (kW vs kWh). The other roles are deliberately left unfiltered because their
# unit varies by wallbox and a filter would hide a real sensor — e.g. `max_power` (dynamic-load
# limit) is reported in **A** by V2C/Pulsar (not kW), `max_current` is the A control, `speed` is
# km/h, `status` is text — and none of them feed the energy/cost maths.
_ROLE_UNITS = {
    "power":  {"w", "kw"},
    "energy": {"wh", "kwh", "mwh"},
}


def entities_for_role(role: str, entities: list[dict], selected_eid: str | None = None) -> list[dict]:
    """Subset of `entities` offerable for `role` in normal mode: only those whose unit (or the
    role's device_class) fits, so the unit can't be mismatched by hand. The currently-mapped
    entity is always kept so an existing choice is never hidden, and unit-less roles return
    everything. Advanced 'Show all' mode bypasses this entirely (caller passes the full list)."""
    units = _ROLE_UNITS.get(role)
    if not units:
        return entities
    dclass = _ROLE_HINTS[role][2]
    out = []
    for e in entities:
        unit = (e.get("unit") or "").strip().lower()
        if unit in units or (dclass and e.get("device_class") == dclass) or e["entity_id"] == selected_eid:
            out.append(e)
    return out


def auto_map(entities: list[dict]) -> dict:
    """Best-guess role→entity_id from a discovered entity list."""
    mapping = {}
    for role, (domains, keywords, dclass) in _ROLE_HINTS.items():
        best, best_s = None, 0
        for e in entities:
            sc = _score(e, domains, keywords, dclass)
            if sc > best_s:
                best, best_s = e["entity_id"], sc
        if best:
            mapping[role] = best
    return mapping


def filter_device_entities(entities: list[dict], mapping: dict) -> list[dict]:
    """Keep only entities belonging to the same device as the mapped roles, so the
    picker offers the wallbox's own sensors — not every power/energy entity in HA.
    Device is inferred as the longest common id-prefix of the mapped entities."""
    # Anchor on the CORE roles (power + energy): auto-map identifies these most reliably, so a
    # noisier role that fell back to an unrelated entity — e.g. a max-current `number` from a
    # household device — can't collapse the common prefix and silently disable the whole filter
    # (which flooded the picker with every home power sensor on a V2C). Fall back to all mapped
    # roles, then to no filtering.
    ids = [mapping.get(r) for r in ("power", "energy") if mapping.get(r)] or \
          [v for v in mapping.values() if v]
    if not ids:
        return entities

    def tail(eid):
        return eid.split(".", 1)[1] if "." in eid else eid

    tails = [tail(i) for i in ids]
    prefix = tails[0]
    for t in tails[1:]:
        i = 0
        while i < len(prefix) and i < len(t) and prefix[i] == t[i]:
            i += 1
        prefix = prefix[:i]
    prefix = prefix.rstrip("_")
    if len(prefix) < 4:          # couldn't infer a meaningful device → don't filter
        return entities
    return [e for e in entities if prefix in tail(e["entity_id"])]


def get_mapping() -> dict:
    """Saved role→entity_id mapping (JSON in settings), or {}."""
    raw = db_reader.get_setting("wallbox_entities", "")
    if not raw:
        return {}
    try:
        m = json.loads(raw)
        return m if isinstance(m, dict) else {}
    except json.JSONDecodeError:
        return {}


def _num(state: dict | None):
    """Parse a numeric state, returning (value, unit) or (None, unit)."""
    if not state:
        return None, ""
    unit = (state.get("attributes", {}) or {}).get("unit_of_measurement", "")
    raw = state.get("state", "")
    try:
        return float(raw), unit
    except (TypeError, ValueError):
        return None, unit


# Session-stat entities auto-found by keyword (no formal role/mapping needed).
_EXTRA_HINTS = {
    "speed":     ("velocita_di_carica", "charging_speed", "charge_speed"),
    "max_power": ("potenza_massima_disponibile", "max_available_power", "available_power", "disponibile"),
}


def _state_num(st: dict | None):
    if not st:
        return None, ""
    unit = (st.get("attributes", {}) or {}).get("unit_of_measurement", "")
    try:
        return float(st.get("state")), unit
    except (TypeError, ValueError):
        return None, unit


def _static_max_power():
    """User-typed fallback for the wallbox 'max available' tile when no HA sensor exposes it (#111,
    @Wartopia: knows the wallbox max, e.g. 11 kW / 16 A, but the PlugChoice integration has no sensor
    for it). Returns (value, unit) or (None, ''). Display-only — it never feeds the cost."""
    raw = (db_reader.get_setting("wb_max_power_static", "") or "").strip().replace(",", ".")
    if not raw:
        return None, ""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None, ""
    unit = db_reader.get_setting("wb_max_power_unit", "kW") or "kW"
    return val, (unit if unit in ("kW", "A") else "kW")


def get_live() -> dict:
    """Live wallbox data + session stats, from a single bulk /api/states fetch."""
    out = {"configured": False, "power_kw": None, "energy_kwh": None, "status": None,
           "max_current_a": None, "charging": False, "speed": None, "speed_unit": "",
           "max_power": None, "max_power_unit": ""}
    # The switch in Settings, honoured HERE because this is the only door wallbox data comes
    # through — the web tile calls it, and so does the poller (recorder._read_wallbox_energy).
    # It used to gate the wallbox PAGE, the session-energy line and the chart overlay, and nothing
    # else: with the feature off and a mapping still saved, every poll kept reading the meter into
    # `ac_energy_kwh`, and `poller/db.py` bills a home charge on that column whenever it is set. The
    # owner went on being billed at a meter he had switched away from. Off means there is no wallbox
    # data, for anybody. The saved mapping is left alone — off is ignored, not forgotten.
    if db_reader.get_setting("wallbox_enabled", "0") != "1":
        return out
    if not is_configured():
        return out
    try:
        status, body = _request("/api/states")
    except Exception:  # noqa: BLE001
        return out
    if status != 200 or not isinstance(body, list):
        return out

    out["configured"] = True
    index = {s.get("entity_id"): s for s in body}
    m = get_mapping()

    pv, pu = _state_num(index.get(m.get("power", "")))
    if pv is not None:
        out["power_kw"] = pv / 1000 if pu.lower() == "w" else pv  # normalise W→kW
    ev, eu = _state_num(index.get(m.get("energy", "")))
    if ev is not None:
        out["energy_kwh"] = ev / 1000 if eu.lower() == "wh" else ev  # normalise Wh→kWh
    st = index.get(m.get("status", ""))
    out["status"] = st.get("state") if st else None
    out["max_current_a"], _ = _state_num(index.get(m.get("max_current", "")))

    def find(keywords):
        for eid, s in index.items():
            hay = f"{eid} {(s.get('attributes', {}) or {}).get('friendly_name', '')}".lower()
            if ("wallbox" in hay or "pulsar" in hay) and any(k in hay for k in keywords):
                return s
        return None

    def resolve(role):  # mapped entity wins; auto-find only as a fallback
        eid = m.get(role)
        return index.get(eid) if eid else find(_EXTRA_HINTS[role])

    out["speed"], out["speed_unit"] = _state_num(resolve("speed"))
    out["max_power"], out["max_power_unit"] = _state_num(resolve("max_power"))
    if out["max_power"] is None:                         # no sensor → user's static value (#111)
        out["max_power"], out["max_power_unit"] = _static_max_power()

    out["charging"] = out["power_kw"] is not None and out["power_kw"] > 0.05
    return out


# ── Wallbox control (writes to HA) ─────────────────────────────────────────────

def _f(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def call_service(domain: str, service: str, data: dict) -> bool:
    """POST /api/services/<domain>/<service>. True on 2xx."""
    if not is_configured():
        return False
    try:
        status, _ = _request(f"/api/services/{domain}/{service}", method="POST", payload=data)
        return 200 <= status < 300
    except Exception:  # noqa: BLE001
        return False


def get_max_current_config() -> dict | None:
    """Current value + min/max/step of the mapped max-current number entity."""
    eid = get_mapping().get("max_current")
    if not eid or not is_configured():
        return None
    st = get_state(eid)
    if not st:
        return None
    a = st.get("attributes", {}) or {}
    val, _ = _state_num(st)
    return {"value": val, "min": _f(a.get("min"), 6), "max": _f(a.get("max"), 32),
            "step": _f(a.get("step"), 1), "unit": a.get("unit_of_measurement") or "A"}


def set_max_current(value: float) -> bool:
    """Set the wallbox max charging current via number.set_value."""
    eid = get_mapping().get("max_current")
    if not eid:
        return False
    return call_service("number", "set_value", {"entity_id": eid, "value": value})


def epoch(iso: str) -> float | None:
    """ISO-8601 (with offset) → epoch seconds, or None."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def get_history(entity_id: str, start_iso: str, end_iso: str) -> list[tuple[float, float]]:
    """Numeric history of one entity over [start, end] as sorted (epoch, value).

    Uses HA's /api/history/period — no need to store wallbox samples ourselves;
    HA's recorder already has them (default retention ~10 days)."""
    if not entity_id or not is_configured():
        return []
    path = (f"/api/history/period/{quote(start_iso, safe='')}"
            f"?filter_entity_id={quote(entity_id, safe='')}"
            f"&end_time={quote(end_iso, safe='')}"
            f"&minimal_response&significant_changes_only=0")
    try:
        status, body = _request(path)
    except Exception:  # noqa: BLE001
        return []
    if status != 200 or not isinstance(body, list) or not body or not isinstance(body[0], list):
        return []
    # With minimal_response HA sends full attributes only on the FIRST sample, so
    # read the unit there to normalise W→kW (some wallboxes report power in watts).
    unit = ""
    if isinstance(body[0][0], dict):
        unit = (body[0][0].get("attributes", {}) or {}).get("unit_of_measurement", "")
    scale = 0.001 if unit.lower() == "w" else 1.0
    out: list[tuple[float, float]] = []
    for s in body[0]:
        ts = s.get("last_changed") or s.get("last_updated")
        ep = epoch(ts) if ts else None
        try:
            val = float(s.get("state")) * scale
        except (TypeError, ValueError):
            continue  # skip unavailable/unknown
        if ep is not None:
            out.append((ep, val))
    out.sort()
    return out
