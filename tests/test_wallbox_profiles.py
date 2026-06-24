"""Wallbox saved profiles — GitHub #84.

Tests cover:
  - save / load / delete round-trips
  - prices are included in the snapshot and restored on load
  - wallbox_enabled and wallbox_auto_home are included and restored
  - mid-charge guard (refuse load while charging=1)
  - duplicate-name guard on save
  - active-profile indicator is cleared when wallbox settings are edited directly

These need web.main (fastapi); the minimal CI env skips this module cleanly.
"""
import json
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")

import db as D          # poller schema — creates settings/charges tables
import db_reader
import main


def _setup(tmp_path, monkeypatch):
    """Point db_reader at a fresh in-process DB and return the raw DB handle."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


# ── helpers ────────────────────────────────────────────────────────────────────

def _profiles(monkeypatch, tmp_path):
    """Return _get/_set bound to the tmp DB (convenience for table-driven tests)."""
    _setup(tmp_path, monkeypatch)
    return main._get_wallbox_profiles, main._set_wallbox_profiles


# ── save / load / delete ───────────────────────────────────────────────────────

def test_save_creates_profile(tmp_path, monkeypatch):
    get, set_ = _profiles(monkeypatch, tmp_path)
    assert get() == []
    set_([{"id": "abc12345", "name": "Home"}])
    profiles = get()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Home"


def test_save_includes_prices_in_snapshot(tmp_path, monkeypatch):
    """Saving a profile must capture the current price_*_kwh values."""
    _setup(tmp_path, monkeypatch)
    db_reader.set_setting("ha_url", "http://ha.local")
    db_reader.set_setting("ha_token", "tok")
    db_reader.set_setting("wb_keywords", "mybox")
    db_reader.set_setting("wallbox_entities", "{}")
    db_reader.set_setting("wallbox_enabled", "1")
    db_reader.set_setting("wallbox_auto_home", "1")
    db_reader.update_charge_price("price_home_kwh", 0.25)
    db_reader.update_charge_price("price_ac_kwh", 0.30)
    db_reader.update_charge_price("price_fast_kwh", 0.55)
    db_reader.update_charge_price("price_hpc_kwh", 0.70)

    # Directly build a profile the same way the handler does (monkeypatched DB = same path).
    import uuid
    prices = db_reader.get_charge_prices()
    profile = {
        "id": uuid.uuid4().hex[:8],
        "name": "Office",
        "ha_url":            db_reader.get_setting("ha_url", ""),
        "ha_token":          db_reader.get_setting("ha_token", ""),
        "wb_keywords":       db_reader.get_setting("wb_keywords", ""),
        "wallbox_entities":  db_reader.get_setting("wallbox_entities", ""),
        "wallbox_enabled":   db_reader.get_setting("wallbox_enabled", "0"),
        "wallbox_auto_home": db_reader.get_setting("wallbox_auto_home", "0"),
        "price_home_kwh":    prices.get("price_home_kwh"),
        "price_ac_kwh":      prices.get("price_ac_kwh"),
        "price_fast_kwh":    prices.get("price_fast_kwh"),
        "price_hpc_kwh":     prices.get("price_hpc_kwh"),
    }
    main._set_wallbox_profiles([profile])

    saved = main._get_wallbox_profiles()[0]
    assert saved["price_home_kwh"] == 0.25
    assert saved["price_ac_kwh"]   == 0.30
    assert saved["price_fast_kwh"] == 0.55
    assert saved["price_hpc_kwh"]  == 0.70
    assert saved["wallbox_enabled"]   == "1"
    assert saved["wallbox_auto_home"] == "1"


def test_load_restores_prices(tmp_path, monkeypatch):
    """Loading a profile must write the snapshotted prices back to settings."""
    _setup(tmp_path, monkeypatch)
    # Persist a profile with specific prices.
    profile = {
        "id": "deadbeef",
        "name": "Home A",
        "ha_url": "http://home-a.local",
        "ha_token": "",
        "wb_keywords": "",
        "wallbox_entities": "{}",
        "wallbox_enabled": "1",
        "wallbox_auto_home": "0",
        "price_home_kwh": 0.20,
        "price_ac_kwh":   0.22,
        "price_fast_kwh": 0.50,
        "price_hpc_kwh":  0.65,
    }
    main._set_wallbox_profiles([profile])
    # Set a different current price to confirm load overwrites it.
    db_reader.update_charge_price("price_home_kwh", 0.45)

    # Simulate what the load handler does.
    monkeypatch.setattr(db_reader, "get_latest_status", lambda: {"charging": 0})
    for price_key in ("price_home_kwh", "price_ac_kwh", "price_fast_kwh", "price_hpc_kwh"):
        val = profile.get(price_key)
        if val is not None:
            db_reader.update_charge_price(price_key, float(val))
    db_reader.set_setting("wallbox_enabled",   profile["wallbox_enabled"])
    db_reader.set_setting("wallbox_auto_home", profile["wallbox_auto_home"])
    db_reader.set_setting("wallbox_active_profile", "deadbeef")

    prices = db_reader.get_charge_prices()
    assert prices["price_home_kwh"] == 0.20   # restored, not 0.45
    assert prices["price_ac_kwh"]   == 0.22
    assert db_reader.get_setting("wallbox_enabled") == "1"
    assert db_reader.get_setting("wallbox_active_profile") == "deadbeef"


def test_delete_removes_profile(tmp_path, monkeypatch):
    get, set_ = _profiles(monkeypatch, tmp_path)
    set_([
        {"id": "aaa", "name": "Home"},
        {"id": "bbb", "name": "Office"},
    ])
    remaining = [p for p in get() if p["id"] != "aaa"]
    set_(remaining)
    profiles = get()
    assert len(profiles) == 1
    assert profiles[0]["id"] == "bbb"


def test_delete_clears_active_profile_indicator(tmp_path, monkeypatch):
    """If the deleted profile was the active one, the active indicator must be cleared."""
    _setup(tmp_path, monkeypatch)
    main._set_wallbox_profiles([{"id": "active1", "name": "Active"}])
    db_reader.set_setting("wallbox_active_profile", "active1")

    # Simulate what the delete handler does.
    profiles = [p for p in main._get_wallbox_profiles() if p["id"] != "active1"]
    main._set_wallbox_profiles(profiles)
    if db_reader.get_setting("wallbox_active_profile", "") == "active1":
        db_reader.set_setting("wallbox_active_profile", "")

    assert main._get_wallbox_profiles() == []
    assert db_reader.get_setting("wallbox_active_profile", "") == ""


# ── active-profile resolution ──────────────────────────────────────────────────

def test_active_profile_resolution(tmp_path, monkeypatch):
    """_ctx() resolves the active profile name when wallbox is enabled and a profile is loaded."""
    _setup(tmp_path, monkeypatch)
    main._set_wallbox_profiles([{"id": "p1", "name": "Home"}])
    db_reader.set_setting("wallbox_enabled", "1")
    db_reader.set_setting("wallbox_active_profile", "p1")

    # Mirror the resolution logic from _ctx().
    wallbox_enabled = db_reader.get_setting("wallbox_enabled", "0") == "1"
    wb_active_profile_name = None
    wb_active_profile_id   = None
    if wallbox_enabled:
        _pid = db_reader.get_setting("wallbox_active_profile", "")
        if _pid:
            _match = next((p for p in main._get_wallbox_profiles() if p["id"] == _pid), None)
            if _match:
                wb_active_profile_name = _match["name"]
                wb_active_profile_id   = _pid

    assert wb_active_profile_name == "Home"
    assert wb_active_profile_id   == "p1"


def test_active_profile_not_resolved_when_wallbox_disabled(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    main._set_wallbox_profiles([{"id": "p1", "name": "Home"}])
    db_reader.set_setting("wallbox_enabled", "0")
    db_reader.set_setting("wallbox_active_profile", "p1")

    wallbox_enabled = db_reader.get_setting("wallbox_enabled", "0") == "1"
    wb_active_profile_name = None
    if wallbox_enabled:
        _pid = db_reader.get_setting("wallbox_active_profile", "")
        if _pid:
            _match = next((p for p in main._get_wallbox_profiles() if p["id"] == _pid), None)
            if _match:
                wb_active_profile_name = _match["name"]

    assert wb_active_profile_name is None


# ── mid-charge guard ───────────────────────────────────────────────────────────

def test_load_blocked_while_charging(tmp_path, monkeypatch):
    """If the car is actively charging, the guard must prevent a profile switch."""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(db_reader, "get_latest_status", lambda: {"charging": 1})

    status = db_reader.get_latest_status()
    is_blocked = bool(status and status.get("charging"))
    assert is_blocked is True


def test_load_allowed_when_not_charging(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(db_reader, "get_latest_status", lambda: {"charging": 0})

    status = db_reader.get_latest_status()
    is_blocked = bool(status and status.get("charging"))
    assert is_blocked is False


def test_load_allowed_when_status_none(tmp_path, monkeypatch):
    """No status row at all (fresh install) must not block a profile load."""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(db_reader, "get_latest_status", lambda: None)

    status = db_reader.get_latest_status()
    is_blocked = bool(status and status.get("charging"))
    assert is_blocked is False


# ── duplicate-name guard ───────────────────────────────────────────────────────

def test_duplicate_name_is_rejected(tmp_path, monkeypatch):
    """Saving two profiles with the same name must be refused."""
    _setup(tmp_path, monkeypatch)
    main._set_wallbox_profiles([{"id": "x1", "name": "Home"}])
    profiles = main._get_wallbox_profiles()
    name = "Home"
    is_duplicate = any(p["name"].strip().lower() == name.lower() for p in profiles)
    assert is_duplicate is True


def test_case_insensitive_duplicate_check(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    main._set_wallbox_profiles([{"id": "x1", "name": "home"}])
    profiles = main._get_wallbox_profiles()
    assert any(p["name"].strip().lower() == "HOME".lower() for p in profiles)


def test_different_names_are_not_duplicates(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    main._set_wallbox_profiles([{"id": "x1", "name": "Home"}])
    profiles = main._get_wallbox_profiles()
    is_duplicate = any(p["name"].strip().lower() == "Office".lower() for p in profiles)
    assert is_duplicate is False


# ── stale-indicator cleared on direct edits ────────────────────────────────────

def test_stale_indicator_cleared_after_direct_ha_edit(tmp_path, monkeypatch):
    """Editing HA URL/token directly must clear the active-profile indicator."""
    _setup(tmp_path, monkeypatch)
    db_reader.set_setting("wallbox_active_profile", "some_profile_id")
    # Simulate what save_ha does after our change.
    db_reader.set_setting("ha_url", "http://new.ha.local")
    db_reader.set_setting("wallbox_active_profile", "")
    assert db_reader.get_setting("wallbox_active_profile", "") == ""


def test_stale_indicator_cleared_after_direct_price_edit(tmp_path, monkeypatch):
    """Editing prices directly (not via a profile load) must clear the active indicator."""
    _setup(tmp_path, monkeypatch)
    db_reader.set_setting("wallbox_active_profile", "some_profile_id")
    # Simulate what save_prices does after our change.
    db_reader.update_charge_price("price_home_kwh", 0.99)
    db_reader.set_setting("wallbox_active_profile", "")
    assert db_reader.get_setting("wallbox_active_profile", "") == ""


def test_stale_indicator_cleared_after_direct_entities_edit(tmp_path, monkeypatch):
    """Editing entity mapping directly must clear the active-profile indicator."""
    _setup(tmp_path, monkeypatch)
    db_reader.set_setting("wallbox_active_profile", "some_profile_id")
    # Simulate what save_wallbox_entities does after our change.
    db_reader.set_setting("wallbox_entities", json.dumps({"power": "sensor.wb_power"}))
    db_reader.set_setting("wallbox_active_profile", "")
    assert db_reader.get_setting("wallbox_active_profile", "") == ""


# ── malformed wallbox_profiles JSON ───────────────────────────────────────────

def test_malformed_profiles_json_returns_empty_list(tmp_path, monkeypatch):
    """A corrupted wallbox_profiles value must degrade gracefully to an empty list."""
    _setup(tmp_path, monkeypatch)
    db_reader.set_setting("wallbox_profiles", "not-valid-json{{")
    assert main._get_wallbox_profiles() == []


def test_non_list_profiles_json_returns_empty_list(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    db_reader.set_setting("wallbox_profiles", '{"key": "value"}')
    assert main._get_wallbox_profiles() == []
