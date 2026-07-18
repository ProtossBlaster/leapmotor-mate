"""Regression tests for v1.11.16:

1. Overlapping/orphan charges must NOT let one charge's power-window or split-cost absorb a
   LATER charge's power samples (GitHub #23: two charges showed the same "Charged" end time).
2. The split-cost integration must skip multi-hour gaps between power bursts (no phantom interval).
3. A day-restricted off-peak band crossing midnight prices its after-midnight hours correctly.
4. The poller must not fragment one plug-in into two overlapping charges, and orphan-close must
   cap the orphan's ended_at at the next charge's start.
"""
import sqlite3
import types
from datetime import datetime, timedelta, timezone

import db_reader


# ── helpers ───────────────────────────────────────────────────────────────────
def _dense(start_iso, end_iso, step_min=5, kw=5.0, volt=230.0):
    """charging=1 position rows every step_min from start..end at constant kW."""
    amps = kw * 1000.0 / volt
    t, end = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
    out = []
    while t <= end:
        out.append((t.isoformat(), 1, volt, amps))
        t += timedelta(minutes=step_min)
    return out


def _db(charges, positions):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE charges (id INT, started_at TEXT, ended_at TEXT, vehicle_id INTEGER DEFAULT 1)")
    con.executemany("INSERT INTO charges (id, started_at, ended_at) VALUES (?,?,?)", charges)
    con.execute("CREATE TABLE positions (recorded_at TEXT, charging INT, "
                "charge_voltage_v REAL, charge_current_a REAL)")
    con.executemany("INSERT INTO positions VALUES (?,?,?,?)", positions)
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    con.commit()
    return con


# ── 1. window cap at the next charge's start ──────────────────────────────────
def test_window_capped_at_next_charge_start():
    # One physical plug-in fragmented into A (orphan closed late, overlaps B) + B (morning top-up).
    charges = [
        (1, "2026-06-07T20:00:00+00:00", "2026-06-08T08:25:00+00:00"),   # A: ended_at bleeds past B
        (2, "2026-06-08T08:00:00+00:00", "2026-06-08T08:30:00+00:00"),   # B
    ]
    positions = [
        ("2026-06-07T21:35:00+00:00", 1, 230, 21.7),    # burst A start
        ("2026-06-07T23:00:00+00:00", 1, 230, 21.7),    # burst A end
        ("2026-06-08T08:05:00+00:00", 1, 230, 21.7),    # burst B start (belongs to charge 2)
        ("2026-06-08T08:13:00+00:00", 1, 230, 21.7),    # burst B end
    ]
    con = _db(charges, positions)
    # A capped at B.start (08:00Z, exclusive) → ends at burst-A's last power, NOT burst-B's 08:13.
    rs, re = db_reader._charge_active_window(con, "2026-06-07T20:00:00+00:00", "2026-06-08T08:25:00+00:00")
    assert rs == "2026-06-07T21:35:00+00:00"
    assert re == "2026-06-07T23:00:00+00:00"            # the leak (08:13) is gone
    # B sees only its own samples.
    rs2, re2 = db_reader._charge_active_window(con, "2026-06-08T08:00:00+00:00", "2026-06-08T08:30:00+00:00")
    assert (rs2, re2) == ("2026-06-08T08:05:00+00:00", "2026-06-08T08:13:00+00:00")


def test_power_curve_capped_at_next_charge_start(monkeypatch):
    # GitHub #24: the per-sample power curve (which feeds the AC-vs-DC comparison and the HOME
    # cost's AC energy) must also be capped at the next charge's start — an orphan whose ended_at
    # bled past a later charge would otherwise integrate that charge's wallbox AC → absurd cost.
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE charges (id INT, started_at TEXT, ended_at TEXT, vehicle_id INTEGER DEFAULT 1)")
    con.executemany("INSERT INTO charges (id, started_at, ended_at) VALUES (?,?,?)", [
        (1, "2026-06-07T20:00:00+00:00", "2026-06-08T08:25:00+00:00"),   # A: ended_at bleeds past B
        (2, "2026-06-08T08:00:00+00:00", "2026-06-08T08:30:00+00:00"),   # B
    ])
    con.execute("CREATE TABLE positions (recorded_at TEXT, charging INT, "
                "charge_voltage_v REAL, charge_current_a REAL, soc REAL)")
    con.executemany("INSERT INTO positions VALUES (?,?,?,?,?)", [
        ("2026-06-07T21:35:00+00:00", 1, 230, 21.7, 40),   # A
        ("2026-06-07T23:00:00+00:00", 1, 230, 21.7, 55),   # A
        ("2026-06-08T08:05:00+00:00", 1, 230, 21.7, 60),   # B (belongs to charge 2)
        ("2026-06-08T08:13:00+00:00", 1, 230, 21.7, 62),   # B
    ])
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    # A's curve must stop before B's samples (08:05/08:13 are excluded by the cap)
    assert db_reader.get_charge_power_curve(1)["times"] == [
        "2026-06-07T21:35:00+00:00", "2026-06-07T23:00:00+00:00"]
    # B sees only its own samples (no charge after it → uncapped, unchanged behaviour)
    assert db_reader.get_charge_power_curve(2)["times"] == [
        "2026-06-08T08:05:00+00:00", "2026-06-08T08:13:00+00:00"]


def test_window_without_charges_table_is_unclamped():
    # Isolated DB with no charges table → no cap, original behaviour (backward compatible).
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE positions (recorded_at TEXT, charging INT)")
    con.executemany("INSERT INTO positions VALUES (?,?)",
                    [("2026-06-02T16:48:59+00:00", 1), ("2026-06-02T21:18:36+00:00", 1)])
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    con.commit()
    rs, re = db_reader._charge_active_window(con, "2026-06-02T16:48:39+00:00", "2026-06-02T23:53:43+00:00")
    assert rs == "2026-06-02T16:48:59+00:00" and re == "2026-06-02T21:18:36+00:00"


# ── 2. split cost skips a multi-hour gap (no phantom interval) ─────────────────
def test_split_cost_skips_long_gap(monkeypatch):
    monkeypatch.setattr(db_reader, "_local_tz", lambda: timezone.utc)   # local == UTC → deterministic bands
    monkeypatch.setattr(db_reader, "get_cost_config",
                        lambda: {"mode": "tou", "method": "split",
                                 "bands": [{"start": "23:30", "end": "07:30", "days": list(range(7)),
                                            "prices": {"HOME": 0.13}}]})
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: {"price_home_kwh": 0.25})
    pos = (_dense("2026-06-07T23:35:00+00:00", "2026-06-08T01:00:00+00:00")     # burst A (off-peak)
           + _dense("2026-06-08T10:00:00+00:00", "2026-06-08T10:15:00+00:00"))  # burst B (peak), 9h gap
    con = _db([(1, "2026-06-07T22:00:00+00:00", "2026-06-08T10:30:00+00:00")], pos)
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    charge = {"location_type": "HOME", "energy_added_kwh": 8.2,
              "started_at": "2026-06-07T22:00:00+00:00", "ended_at": "2026-06-08T10:30:00+00:00"}
    cost = db_reader.compute_cost(charge)
    # Only the two real bursts are priced (gap skipped) → ~1.21. With the phantom gap it was ~1.09.
    assert 1.15 <= cost <= 1.27


# ── 3. day-restricted midnight-crossing band ──────────────────────────────────
def test_match_band_midnight_crossing_day_restricted():
    bands = [{"start": "23:30", "end": "07:30", "days": [5], "prices": {"HOME": 0.13}}]  # Sat only (Mon=0)
    assert db_reader._match_band(bands, 5, 23 * 60 + 35) is not None   # Sat 23:35 → pre-midnight, matches
    assert db_reader._match_band(bands, 6, 1 * 60) is not None         # Sun 01:00 → prev day (Sat) → matches
    assert db_reader._match_band(bands, 6, 23 * 60 + 35) is None       # Sun 23:35 → Sun not in days → no
    assert db_reader._match_band(bands, 0, 1 * 60) is None             # Mon 01:00 → prev (Sun) not in days
    allb = [{"start": "23:30", "end": "07:30", "days": list(range(7)), "prices": {"HOME": 0.13}}]
    assert db_reader._match_band(allb, 2, 2 * 60) is not None          # all-days band still covers 02:00


# ── 4. poller: no duplicate charge on resume + orphan-close clamps ended_at ────
def test_recorder_does_not_duplicate_charge_on_resume():
    import recorder as R
    from state_machine import StateEvent, State

    class _FakeDB:
        def __init__(self):
            self.creates = 0
        def create_charge(self, *a):
            self.creates += 1
            return 99
        def finalize_charge(self, *a, **k):
            pass

    db = _FakeDB()
    rec = R.Recorder(db, vehicle_id=1)
    # Hermetic: on a host with a live HA wallbox, _read_wallbox_energy() would return a real
    # reading and the recorder would call wallbox methods the fake doesn't have.
    rec._read_wallbox_energy = lambda: None
    data = types.SimpleNamespace(soc=50, latitude=1.0, longitude=2.0)
    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.CHARGING, data), data)
    assert db.creates == 1 and rec._active_charge_id == 99
    # Re-entering CHARGING (e.g. after an OFFLINE gap) with a charge still open → resume, not duplicate.
    rec._handle_event(StateEvent(State.OFFLINE, State.CHARGING, data), data)
    assert db.creates == 1


def test_close_orphan_charges_clamps_ended_at_before_next(tmp_path):
    import db as D

    db = D.Database(str(tmp_path / "t.db"))
    c = db._conn
    c.execute("INSERT INTO charges (id,vehicle_id,started_at,start_soc) "
              "VALUES (1,1,'2026-06-07T20:00:00+00:00',20)")                     # orphan A (ended NULL)
    c.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc) "
              "VALUES (2,1,'2026-06-08T08:00:00+00:00','2026-06-08T08:30:00+00:00',50,55)")
    c.execute("INSERT INTO positions (vehicle_id,recorded_at,soc,charging) "
              "VALUES (1,'2026-06-07T23:00:00+00:00',40,1)")                      # inside A's span
    c.execute("INSERT INTO positions (vehicle_id,recorded_at,soc,charging) "
              "VALUES (1,'2026-06-08T08:13:00+00:00',55,1)")                      # AFTER B started
    c.commit()
    assert db.close_orphan_charges(1) == 1
    ended = c.execute("SELECT ended_at FROM charges WHERE id=1").fetchone()["ended_at"]
    assert ended == "2026-06-07T23:00:00+00:00"          # capped before B, NOT the 08:13 latest position
