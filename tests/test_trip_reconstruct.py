"""Reconstructed trips — feature #118 (riri19).

A drive that happens while the car is offline/asleep to the cloud (or the poller is down) is never
seen live: no DRIVING poll ever fires, the state machine never opens a trip, and the trip is lost —
exactly like the missed-charge case (#29), but for driving. The only trace is the ODOMETER that
jumped while the car looked parked. `_maybe_reconstruct_trip` detects that and rebuilds the trip from
the odometer delta (distance) + SoC delta (energy), with NO GPS and marked reconstructed=1.

These pin the DETECTION (the crux of riri19's question — how do we know a trip is missing?) and the
key constraint he raised: a reconstructed trip can NEVER be getEC-converted, because the cloud has no
record of a trip it never saw.
"""
import datetime

from client import VehicleData
from state_machine import State
import db as D
import recorder as R
import db_reader


def _vd(soc, odo, *, gear="P", speed=0.0, charging=0, plug=False, lat=45.0, lon=9.0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=0, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="parked",
        charging_status=charging, charge_power_kw=0.0, latitude=lat, longitude=lon,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=plug, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0)


class _CountDB:
    """Minimal stub for the DECISION tests: records reconstructed trips, refuses the rest."""
    def __init__(self):
        self.trips = []

    def create_reconstructed_trip(self, vid, start_soc, start_odo, started_at, data):
        self.trips.append((start_odo, data.odometer_km, start_soc, data.soc))
        return len(self.trips)

    def charge_open_since(self, vid, since):
        return False

    def charging_read_since(self, vid, since, frame_ts):
        return False


def _rec(state=State.PARKED_ACTIVE, last_odo=1000.0, last_soc=60.0):
    rec = R.Recorder(_CountDB(), vehicle_id=1)
    rec._sm.state = state
    rec._active_trip_id = None
    rec._odometer_reading = R.OdometerReading(last_odo, last_soc, "2026-07-04T20:00:00+00:00")
    return rec


# ── DETECTION: the crux — an odometer jump while parked = a drive we missed ───
def test_reconstructs_on_parked_odometer_jump():
    rec = _rec(last_odo=1000.0, last_soc=60.0)
    rec._maybe_reconstruct_trip(_vd(53.0, 1015.0))       # +15 km while parked, SoC fell → a real drive
    assert rec._db.trips == [(1000.0, 1015.0, 60.0, 53.0)]
    assert rec._odometer_reading.odometer_km == 1015.0   # baseline advanced (won't re-fire next poll)


# ── NEGATIVES: none of these must invent a phantom trip ───────────────────────
def test_no_reconstruct_while_driving():
    rec = _rec(state=State.DRIVING, last_odo=1000.0)     # a LIVE trip is running
    rec._maybe_reconstruct_trip(_vd(53.0, 1015.0, gear="D", speed=50.0))
    assert rec._db.trips == []                            # the live path records this one, with GPS


def test_no_reconstruct_when_trip_already_open():
    rec = _rec(last_odo=1000.0)
    rec._active_trip_id = 7
    rec._maybe_reconstruct_trip(_vd(53.0, 1015.0))
    assert rec._db.trips == []


def test_no_reconstruct_when_parked_still():
    rec = _rec(last_odo=1000.0, last_soc=60.0)
    rec._maybe_reconstruct_trip(_vd(59.5, 1000.0))       # vampire drain: SoC drifts, odometer unchanged
    assert rec._db.trips == []


def test_no_reconstruct_on_odometer_glitch():
    rec = _rec(last_odo=1000.0)
    rec._maybe_reconstruct_trip(_vd(53.0, 0.0))          # a 0 glitch reading must not be a huge "trip"
    assert rec._db.trips == []
    rec._maybe_reconstruct_trip(_vd(53.0, 1015.0))       # …nor the baseline the next one is measured from
    assert rec._db.trips == [(1000.0, 1015.0, 60.0, 53.0)]


def test_no_reconstruct_when_soc_rose():
    rec = _rec(last_odo=1000.0, last_soc=40.0)
    rec._maybe_reconstruct_trip(_vd(70.0, 1000.0))       # SoC rose = a charge, not a drive (odo unchanged)
    assert rec._db.trips == []


def test_no_reconstruct_sub_1km():
    rec = _rec(last_odo=1000.0)
    rec._maybe_reconstruct_trip(_vd(59.0, 1000.5))       # below the 1 km floor (whole-km odometer noise)
    assert rec._db.trips == []


# ── THE ROW: distance from odo, energy from SoC, NO GPS, marked reconstructed ─
def test_reconstructed_trip_row(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(60.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    tid = db.create_reconstructed_trip(vid, 60.0, 1000.0, "2026-07-04T20:00:00+00:00", _vd(53.0, 1015.0))
    row = db._conn.execute("SELECT * FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["reconstructed"] == 1
    assert row["distance_km"] == 15.0                    # from the odometer delta
    assert row["start_soc"] == 60.0 and row["end_soc"] == 53.0
    assert row["start_lat"] is None and row["end_lat"] is None    # NO GPS → map shows no route
    assert row["ended_at"] is not None
    assert row["ec_stable"] == 1                         # pinned un-enrichable
    # efficiency = ΔSoC×capacity / distance = (7/100·60) / 15 · 100 = 28.0 kWh/100km
    assert abs(row["efficiency_kwh_100km"] - 28.0) < 0.1


def test_sub_1km_row_is_none(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    vid = db.ensure_vehicle("TESTVIN", "B10")
    assert db.create_reconstructed_trip(vid, 60.0, 1000.0, "2026-07-04T20:00:00+00:00", _vd(59.0, 1000.5)) is None
    assert db._conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 0


# ── THE CONSTRAINT riri19 raised: getEC can NEVER convert a reconstructed trip ─
def test_getec_enrichment_skips_reconstructed(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path / "t.db"))
    vid = db.ensure_vehicle("TESTVIN", "B10")
    now = datetime.datetime.now(datetime.timezone.utc)
    start = (now - datetime.timedelta(hours=1)).isoformat()
    end = (now - datetime.timedelta(minutes=30)).isoformat()   # old enough + not too old → EC-eligible
    db._conn.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, reconstructed)"
                     " VALUES (?,?,?,?,1)", (vid, start, end, 15.0))       # reconstructed → must be skipped
    db._conn.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, reconstructed)"
                     " VALUES (?,?,?,?,0)", (vid, start, end, 20.0))       # normal → must be queued
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    dists = [t["distance_km"] for t in db_reader.get_trips_needing_ec((now - datetime.timedelta(days=1)).isoformat())]
    assert 20.0 in dists          # the normal trip is queued for the official getEC value
    assert 15.0 not in dists      # the reconstructed one is NOT — the cloud has no record of it


# ── ROBUSTNESS for riri19's flaky car↔cloud link ─────────────────────────────
def test_odometer_glitch_distance_rejected(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    vid = db.ensure_vehicle("TESTVIN", "B10")
    # a 5000 km "jump" is an odometer glitch, not a drive → rejected (would poison the stats otherwise)
    assert db.create_reconstructed_trip(vid, 60.0, 1000.0, "2026-07-04T20:00:00+00:00", _vd(50.0, 6000.0)) is None
    assert db._conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 0


def test_long_offline_gap_nulls_duration_but_keeps_trip(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(60.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    # 15 km drive but the offline GAP spans ~10 h (car sat offline before/after) → implied avg ~1.5 km/h →
    # duration is unreliable → NULL, but distance/energy still recorded so the trip still counts in stats.
    start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=10)).isoformat()
    tid = db.create_reconstructed_trip(vid, 60.0, 1000.0, start, _vd(53.0, 1015.0))
    row = db._conn.execute("SELECT * FROM trips WHERE id=?", (tid,)).fetchone()
    assert row is not None and row["distance_km"] == 15.0
    assert row["duration_min"] is None                  # gap padded with parked time → duration dropped
    assert row["efficiency_kwh_100km"] is not None       # distance/energy/efficiency still contribute


def test_plausible_gap_keeps_duration(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(60.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)).isoformat()
    tid = db.create_reconstructed_trip(vid, 60.0, 1000.0, start, _vd(53.0, 1015.0))   # 15 km / 30 min = 30 km/h
    row = db._conn.execute("SELECT duration_min FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["duration_min"] is not None and 28 <= row["duration_min"] <= 32


# ── Silvio's ask: reconstructed trips DO count in the statistics ──────────────
def test_reconstructed_trip_counts_in_stats(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(60.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)).isoformat()
    db.create_reconstructed_trip(vid, 60.0, 1000.0, start, _vd(53.0, 1015.0))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    s = db_reader.get_stats_summary()
    assert s["trip_count"] == 1 and s["total_km"] == 15.0    # counted in the totals
    trips = db_reader.get_trips()
    assert len(trips) == 1 and trips[0]["reconstructed"] == 1  # and listed (with the flag for the badge)


# ── END-TO-END through process(): an offline drive between two parked polls ────
def test_end_to_end_offline_drive(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(60.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    rec = R.Recorder(db, vehicle_id=vid)
    rec.process(_vd(60.0, 1000.0))                       # parked, establishes the odometer baseline
    rec.process(_vd(53.0, 1015.0))                       # back online after a silent 15 km drive
    trips = db._conn.execute("SELECT * FROM trips WHERE reconstructed=1").fetchall()
    assert len(trips) == 1
    assert trips[0]["distance_km"] == 15.0 and trips[0]["start_lat"] is None
