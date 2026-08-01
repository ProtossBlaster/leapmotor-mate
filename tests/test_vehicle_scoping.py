"""Per-vehicle read scoping (Tier-1 multi-car hardening).

Every web read is scoped to the current vehicle via `vehicle_id = COALESCE(?, vehicle_id)` with
`_current_vehicle_id()` (the first/only car today; the selected VIN once the multi-car selector
lands). Two properties matter:
  * SINGLE-CAR / no-vehicle = NO-OP — proven by the whole existing suite staying green, plus the
    no-vehicle fallback here (COALESCE(NULL, vehicle_id) matches every row);
  * TWO-CAR = ISOLATION — a second car's trips/charges/positions must NOT leak into the current
    view (the "Overview shows the other car / stats sum both motors" bug).

`_current_vehicle_id()` resolves to the FIRST vehicle (no selector yet), so these tests assert the
current view shows ONLY vehicle 1, never vehicle 2 — even though vehicle 2 has MORE and LATER rows
(which would dominate an unscoped `ORDER BY ... LIMIT 1` / `SUM`).
"""
import db as D
import db_reader


def _two_car_db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN_A','B10')")
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,'VIN_B','T03')")
    # vehicle 1 (current): 1 trip, 1 charge, an EARLIER position at SoC 55.
    db._conn.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km)"
                     " VALUES (1,'2026-06-01T08:00:00+00:00','2026-06-01T08:30:00+00:00',10,15.0)")
    db._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, start_soc, end_soc)"
                     " VALUES (1,'2026-06-01T20:00:00+00:00','2026-06-01T22:00:00+00:00',20,40,70)")
    db._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc) VALUES (1,'2026-06-01T08:15:00+00:00',55)")
    # vehicle 2 (must NOT leak): MORE rows, and a LATER position at SoC 88 that would win an
    # unscoped "latest".
    for _ in range(2):
        db._conn.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km)"
                         " VALUES (2,'2026-06-02T08:00:00+00:00','2026-06-02T09:00:00+00:00',99,99.0)")
    for _ in range(3):
        db._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, start_soc, end_soc)"
                         " VALUES (2,'2026-06-02T20:00:00+00:00','2026-06-02T22:00:00+00:00',99,10,99)")
    db._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc) VALUES (2,'2026-06-02T09:00:00+00:00',88)")
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db


def test_current_vehicle_id_is_first(tmp_path, monkeypatch):
    _two_car_db(tmp_path, monkeypatch)
    assert db_reader._current_vehicle_id() == 1


def test_current_vehicle_id_none_when_no_vehicle(tmp_path, monkeypatch):
    path = str(tmp_path / "e.db")
    D.Database(path)                                     # schema only, zero vehicles
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    assert db_reader._current_vehicle_id() is None       # → COALESCE no-ops, never filters to empty


def test_get_trips_scoped_to_current(tmp_path, monkeypatch):
    _two_car_db(tmp_path, monkeypatch)
    trips = db_reader.get_trips()
    assert len(trips) == 1                               # vehicle 2's 2 trips excluded
    assert all(t["vehicle_id"] == 1 for t in trips)


def test_get_charges_scoped_to_current(tmp_path, monkeypatch):
    _two_car_db(tmp_path, monkeypatch)
    charges = db_reader.get_charges()
    assert len(charges) == 1                             # vehicle 2's 3 charges excluded
    assert all(c["vehicle_id"] == 1 for c in charges)


def test_get_latest_status_scoped_to_current(tmp_path, monkeypatch):
    _two_car_db(tmp_path, monkeypatch)
    st = db_reader.get_latest_status()
    # vehicle 2's position is LATER (higher id) → an unscoped "latest" returns SoC 88. Scoped → 55.
    assert st is not None and st["vehicle_id"] == 1 and st["soc"] == 55


def test_get_stats_summary_scoped_to_current(tmp_path, monkeypatch):
    """AGGREGATE class: totals must cover only the current car, not sum both motors."""
    _two_car_db(tmp_path, monkeypatch)
    s = db_reader.get_stats_summary()
    assert s["trip_count"] == 1                          # vehicle 2's 2 trips NOT summed in
    assert s["total_km"] == 10.0                         # not 10 + 99 + 99


def test_get_last_charge_end_scoped_to_current(tmp_path, monkeypatch):
    """LATEST-aggregate class: vehicle 2's charges end LATER; unscoped would return one of those."""
    from datetime import timezone
    _two_car_db(tmp_path, monkeypatch)
    end = db_reader.get_last_charge_end()
    assert end is not None
    # vehicle 1's charge ended 2026-06-01T22:00Z; vehicle 2's ended 2026-06-02T22:00Z.
    assert end.astimezone(timezone.utc).isoformat().startswith("2026-06-01T22")


def _two_car_db_with_tracks(tmp_path, monkeypatch):
    """Richer 2-car DB with trip coords + trip_positions for the map/track readers. Vehicle 1 drives
    around Milan (lat ~45.4), vehicle 2 around Rome (lat ~41.9) — so a leak is geographically obvious."""
    path = str(tmp_path / "tk.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN_A','B10')")
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,'VIN_B','T03')")
    for tid in (100, 101):                               # vehicle 1: two trips to the SAME Milan spots
        db._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon)"
                         " VALUES (?,1,'2026-06-01T08:00:00+00:00','2026-06-01T08:30:00+00:00',45.464,9.190,45.470,9.200)", (tid,))
        db._conn.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude)"
                         " VALUES (?,'2026-06-01T08:15:00+00:00',45.464,9.190),(?,'2026-06-01T08:16:00+00:00',45.470,9.200)", (tid, tid))
    for tid in (200, 201):                               # vehicle 2: two trips around Rome (must NOT leak)
        db._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon)"
                         " VALUES (?,2,'2026-06-02T08:00:00+00:00','2026-06-02T09:00:00+00:00',41.900,12.490,41.910,12.500)", (tid,))
        db._conn.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude)"
                         " VALUES (?,'2026-06-02T08:30:00+00:00',41.900,12.490),(?,'2026-06-02T08:31:00+00:00',41.910,12.500)", (tid, tid))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db


def test_get_all_track_scoped_to_current(tmp_path, monkeypatch):
    """trip_positions class — scoped via `trip_id IN (SELECT id FROM trips WHERE vehicle_id=...)`."""
    _two_car_db_with_tracks(tmp_path, monkeypatch)
    # flatten tracks → runs → [lat,lon] pts
    pts = [p for track in db_reader.get_all_track() for run in track for p in run["points"]]
    assert pts and all(lat > 44 for lat, lon in pts)     # only Milan (v1), never Rome (v2, lat ~41.9)


def test_get_frequent_places_scoped_to_current(tmp_path, monkeypatch):
    """No-WHERE class: `FROM trips` with no filter had to GAIN the vehicle scope."""
    _two_car_db_with_tracks(tmp_path, monkeypatch)
    places = db_reader.get_frequent_places()
    assert places and all(p["latitude"] > 44 for p in places)   # Milan only, Rome excluded


def test_null_vehicle_id_backfill_keeps_orphan_rows_visible(tmp_path, monkeypatch):
    """Release safety net: a legacy NULL-vehicle_id trip would be HIDDEN by the scoping (NULL never
    equals the current id). The one-time backfill adopts it into the single vehicle so it stays visible.
    Proves the release can't make anyone's old trips silently vanish."""
    path = str(tmp_path / "n.db")
    db = D.Database(path)                                 # __init__ backfill no-ops (no vehicle yet)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN_A','B10')")
    db._conn.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km)"
                     " VALUES (NULL,'2026-06-01T08:00:00+00:00','2026-06-01T08:30:00+00:00',10)")
    db._conn.execute("DELETE FROM settings WHERE key='null_vehicle_id_backfill_v1'")
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    assert db_reader.get_trips() == []                   # orphan invisible to the scoped read...
    db._backfill_null_vehicle_id()
    trips = db_reader.get_trips()
    assert len(trips) == 1 and trips[0]["vehicle_id"] == 1   # ...adopted → visible again


# ── The VIN-order trap ───────────────────────────────────────────────────────────────────────
# The scoped reads above all pass `_current_vehicle_id()`, which pins `ORDER BY id`. Three
# call-sites resolved the car themselves with a bare `LIMIT 1` instead. `vehicles.vin` is
# UNIQUE, so SQLite keeps a covering index over (vin, rowid): an unordered `SELECT id FROM
# vehicles LIMIT 1` is free to scan THAT index rather than the table, and hand back whichever
# car sorts first BY VIN. `SELECT *` can't use the index (it needs every column) so it still
# scans by rowid — which is why this stayed invisible: the bug depends on the query plan, and
# the fixtures above happen to name car 1 'VIN_A', which sorts first anyway.
#
# Found by seeding a demo database with a second car and diffing every rendered page against
# the same instance without it (2026-07-21).

def _two_car_db_car2_sorts_first(tmp_path, monkeypatch):
    """Same two cars, but car 2's VIN sorts BEFORE car 1's — so an unordered LIMIT 1 names car 2."""
    path = str(tmp_path / "vinorder.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'ZZZ_CAR_ONE','B10')")
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,'AAA_CAR_TWO','T03')")
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db


def test_get_vehicle_follows_the_current_car_not_the_first_vin(tmp_path, monkeypatch):
    _two_car_db_car2_sorts_first(tmp_path, monkeypatch)
    v, _ = db_reader.get_vehicle()
    assert v["id"] == 1 and v["car_type"] == "B10"        # not the alphabetically-first T03


def test_save_fresh_signals_files_the_row_under_the_current_car(tmp_path, monkeypatch):
    """It WRITES: the wrong id would file live telemetry against the other vehicle."""
    db = _two_car_db_car2_sorts_first(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({"1003": 55})            # SoC — enough for a position row
    rows = db._conn.execute("SELECT vehicle_id FROM positions").fetchall()
    assert rows and all(r["vehicle_id"] == 1 for r in rows)


def test_scan_missed_charges_scans_the_current_car(tmp_path, monkeypatch):
    """It reads positions to reconstruct charges, and with apply=True INSERTS them — against the
    wrong car it would both miss the real gaps and invent sessions for a vehicle you aren't
    looking at. Car 2 carries a parked SoC jump that looks exactly like a missed charge; car 1
    has none, so a scan that returns anything is scanning the wrong vehicle."""
    db = _two_car_db_car2_sorts_first(tmp_path, monkeypatch)
    for at, soc in (("2026-06-02T01:00:00+00:00", 20.0), ("2026-06-02T05:00:00+00:00", 80.0)):
        db._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
                         " odometer_km) VALUES (2,?,?,0,0,50000)", (at, soc))
    db._conn.commit()
    assert db_reader.scan_missed_charges(apply=False) == []
