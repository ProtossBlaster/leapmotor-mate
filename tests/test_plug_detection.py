"""Plug-detection regression tests.

THE BUG (observed on a real B10, 2026-06-09): a finished overnight charge kept its
session "open" for ~5.5 min AFTER the cable was physically unplugged. Root cause:
`_is_plugged_in` trusted signal 47 (acInputSlowCharge), which on the B10 LATCHES at 1
after an AC charge and only clears later, when the car's charge controller tears down the
AC subsystem — it does NOT drop on unplug. Signal 1149 (charge connection status) instead
returned to 0 immediately on unplug. The state machine's
`is_charging = charging_status>0 or plug_connected` therefore stayed true on the stuck
plug flag, inflating the session window.

THE FIX: derive the plug from signal 1149, gated by motion (1149 reads 1 spuriously during
regen at speed, so it's suppressed while the car moves — you can't be plugged in while
driving anyway). Signal 47 remains a fallback only when 1149 is absent.
"""
import sqlite3
import sys
import types

import client
import db as poller_db
import db_reader


# ── 1) poller _is_plugged_in unit cases ─────────────────────────────────────────
def _sig(**kw):
    """A signal dict defaulting to PARKED (gear P, speed 0); override per case."""
    base = {"1010": 0, "1319": 0}   # parked, stationary
    base.update({k: v for k, v in kw.items()})
    return base


def test_stuck_47_after_charge_reads_unplugged():
    """The exact bug: parked, charge done, signal 47 latched at 1 but 1149 already 0 → NOT
    plugged (so the session can close)."""
    assert client._is_plugged_in(_sig(**{"47": 1, "1149": 0})) is False


def test_1149_connected_is_plugged():
    assert client._is_plugged_in(_sig(**{"1149": 1})) is True   # connected
    assert client._is_plugged_in(_sig(**{"1149": 2})) is True   # charging


def test_1149_zero_is_unplugged_even_if_47_set():
    # 1149 is authoritative when present; a stale/latched 47 must not override it.
    assert client._is_plugged_in(_sig(**{"1149": 0, "47": 1})) is False


def test_regen_at_speed_is_not_plugged():
    """During regen braking 1149 reads 1 spuriously; the motion gate must suppress it so a
    drive is never mistaken for a charge session."""
    # by gear (D)
    assert client._is_plugged_in({"1010": 3, "1319": 0, "1149": 1, "47": 0}) is False
    # by speed (gear lagging at P but moving)
    assert client._is_plugged_in({"1010": 0, "1319": 40, "1149": 1, "47": 0}) is False


def test_legacy_fallback_to_47_when_1149_absent():
    # Other models without 1149: fall back to 47 (still motion-gated).
    assert client._is_plugged_in(_sig(**{"47": 1})) is True
    assert client._is_plugged_in(_sig(**{"47": 0})) is False
    assert client._is_plugged_in(_sig()) is False               # neither signal → unplugged


def test_fallback_47_still_motion_gated():
    # Even the legacy 47 fallback must not fire while moving.
    assert client._is_plugged_in({"1010": 3, "1319": 50, "47": 1}) is False


# ── 2) _parse_signal integration: the frozen real-world snapshot ────────────────
def test_parse_signal_frozen_snapshot_not_plugged():
    """The actual frozen cloud snapshot from the bug (SoC 90, current 0.1 A, parked,
    47=1, 1149=0) must parse to plug_connected=False and charging_status=0 → session
    closes."""
    frozen = {
        "1010": 0, "1319": 0.0, "100003": 90.0, "1204": 90,
        "1178": 0.1, "1177": 424.7, "47": 1, "1149": 0,
    }
    data = client._parse_signal("VIN123", frozen)
    assert data.plug_connected is False
    assert data.charging_status == 0
    # And the state-machine charge predicate (the thing that kept the session open) is now False.
    assert (data.charging_status > 0 or data.plug_connected) is False


def test_parse_signal_active_charge_is_plugged():
    charging = {
        "1010": 0, "1319": 0.0, "100003": 55.0,
        "1178": 16.0, "1177": 230.0, "1149": 2, "1200": 120,
    }
    data = client._parse_signal("VIN123", charging)
    assert data.plug_connected is True
    assert data.charging_status == 1


# ── 3) web save_fresh_signals path mirrors the poller (no duplicate drift) ───────
def _web_db(tmp_path, monkeypatch):
    path = str(tmp_path / "web.db")
    poller_db.Database(path)                       # build the full schema
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'VIN123')")
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _latest_plug(path):
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    row = con.execute("SELECT plug_connected FROM positions ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return row["plug_connected"]


def _latest_charging(path):
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    row = con.execute("SELECT charging FROM positions ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return row["charging"]


def test_web_save_fresh_signals_stuck_47_unplugged(tmp_path, monkeypatch):
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({"1010": 0, "1319": 0, "1178": 0.1, "1177": 424.7,
                                  "47": 1, "1149": 0, "100003": 90})
    assert _latest_plug(path) == 0


def test_web_save_fresh_signals_connected_plugged(tmp_path, monkeypatch):
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({"1010": 0, "1319": 0, "1178": 16, "1177": 230,
                                  "1149": 2, "1200": 120, "100003": 55})
    assert _latest_plug(path) == 1


def test_web_save_fresh_signals_cable_state_three_still_plugged(tmp_path, monkeypatch):
    # The REEV mid-charge flicker 1→2→3→2: 3 is still connected. The poller has counted it since
    # v2.8.4 (reading it as unplugged shredded slow AC charges — beta #12/#13); this writer is the
    # second copy of that rule and must not disagree with it.
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({"1010": 0, "1319": 0, "1178": 0.5, "1177": 400,
                                  "1149": 3, "1200": 200, "100003": 42})
    assert _latest_plug(path) == 1


def test_web_save_fresh_signals_drive_time_cable_code_not_charging(tmp_path, monkeypatch):
    # 1149==5 is the drive-time cable code the REEVs emit while moving, never a connection. The web
    # writer has its own copy of _is_charging, so it has to exclude 5 too or the two disagree on the
    # same signal — the frame is ebagnoli's, 23/07 08:30 (beta #13), speed still stale at 0.
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({"1010": 0, "1319": 0, "1178": -27.6, "1177": 400,
                                  "1149": 5, "1200": 45, "100003": 25})
    assert _latest_charging(path) == 0
    assert _latest_plug(path) == 0


# ── 4) end-to-end: the charge session must CLOSE on unplug even with 47 latched ──
def test_charge_session_closes_on_unplug_despite_stuck_47():
    """Full chain (signals → _parse_signal → state machine): a charge in progress, then the
    cable is pulled (1149→0) while signal 47 is still LATCHED at 1 — the session must leave
    CHARGING. This is the exact bug; it would FAIL on the old 47-primary plug logic (47=1
    kept plug_connected true → is_charging true → state stuck in CHARGING)."""
    from state_machine import StateMachine, State

    sm = StateMachine()
    charging = client._parse_signal("VIN", {
        "1010": 0, "1319": 0, "100003": 55, "1178": 16, "1177": 230, "1149": 2, "1200": 120})
    sm.update(charging)
    assert sm.state == State.CHARGING

    # Cable pulled: 1149→0, current 0, SoC 90 — but signal 47 STILL latched at 1.
    unplugged = client._parse_signal("VIN", {
        "1010": 0, "1319": 0, "100003": 90, "1178": 0.1, "1177": 424, "47": 1, "1149": 0})
    sm.update(unplugged)
    assert sm.state != State.CHARGING
    assert sm.state in (State.PARKED_ACTIVE, State.PARKED_ALERT, State.PARKED_SLEEP)
