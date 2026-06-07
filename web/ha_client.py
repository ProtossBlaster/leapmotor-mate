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
)
_WB_DEVICE_CLASSES = ("power", "energy", "current", "voltage")


def get_wallbox_keywords() -> tuple:
    """Get wallbox keywords from settings, with fallback to defaults."""
    custom = db_reader.get_setting("wb_keywords", "").strip()
    if custom:
        # Parse comma-separated keywords, strip whitespace from each
        return tuple(k.strip().lower() for k in custom.split(",") if k.strip())
    return _WB_KEYWORDS


def is_custom_keywords() -> bool:
    """Return True if user has set custom keywords (not using defaults)."""
    return bool(db_reader.get_setting("wb_keywords", "").strip())


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


def list_entities(only_wallbox: bool = True) -> list[dict]:
    """Return entities as {entity_id, name, state, unit, device_class}.

    When only_wallbox, keep entities whose id/name matches a charger keyword
    or whose device_class is power/energy/current/voltage. Name matches are
    ranked first so the user's actual wallbox surfaces at the top of the list.
    """
    if not is_configured():
        return []
    try:
        status, body = _request("/api/states")
    except Exception:  # noqa: BLE001
        return []
    if status != 200 or not isinstance(body, list):
        return []

    out: list[dict] = []
    for st in body:
        eid = st.get("entity_id", "")
        attrs = st.get("attributes", {}) or {}
        name = attrs.get("friendly_name") or eid
        dclass = attrs.get("device_class", "")
        unit = attrs.get("unit_of_measurement", "")
        hay = f"{eid} {name}".lower()
        kw_match = any(k in hay for k in get_wallbox_keywords())
        # Only use device_class fallback when using default keywords;
        # when user specifies custom keywords, match only those.
        cls_match = dclass in _WB_DEVICE_CLASSES if not is_custom_keywords() else False
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
    "energy":      (("sensor",),    ("energia_aggiunta", "added_grid_energy", "added_energy", "energy"), "energy"),
    "status":      (("sensor",),    ("descrizione_dello_stato", "status", "stato", "state"), None),
    "max_current": (("number",),    ("corrente_di_carica_massima", "max_charging_current", "massima"), None),
    "speed":       (("sensor",),    ("velocita_di_carica", "charging_speed", "charge_speed"), None),
    "max_power":   (("sensor",),    ("potenza_massima_disponibile", "max_available_power", "available_power", "disponibile"), None),
}
# Names that almost always mean "not the wallbox's own metric" → penalise.
_NEG = ("leapmotor", "differenza", "ups", "green", "scaricata", "deposito", "prezzo", "costo", "filtered")


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
    ids = [v for v in mapping.values() if v]
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


def get_live() -> dict:
    """Live wallbox data + session stats, from a single bulk /api/states fetch."""
    out = {"configured": False, "power_kw": None, "energy_kwh": None, "status": None,
           "max_current_a": None, "charging": False, "speed": None, "speed_unit": "",
           "max_power": None, "max_power_unit": ""}
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
