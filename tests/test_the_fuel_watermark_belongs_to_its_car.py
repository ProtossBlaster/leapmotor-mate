"""The refuel scan's watermark is one install-wide key for a per-vehicle walk (30/08 audit).

`scan_fuel_refuels` reads the tank log of ONE car — `WHERE vehicle_id = ?` — but remembers how far
it got in a single setting shared by the whole install. So the first car to run the scan moves the
mark forward for everybody, and a second REEV added later starts its own first scan already past
its own history: every refuel older than the other car's last reading is skipped, and because the
mark only ever moves forward, they are skipped for good.

Not a rescan hazard: `_flush_fuel_run` refuses a run that already has a `fuel_detected` row at the
same timestamp or a hand-logged `fuel_purchases` row nearby, so widening the window can only find
what was missed, never duplicate what was found.

CI-safe: db_reader + sqlite only, no fastapi.
"""
import sqlite3

import db as poller_db
import db_reader


def _db(tmp_path, monkeypatch):
    path = str(tmp_path / "w.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'CAR-ONE')")
    con.execute("INSERT INTO vehicles (id, vin) VALUES (2, 'CAR-TWO')")
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('reev_tank_l', '50')")
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('is_reev_car-two', '1')")
    con.commit()
    return path, con


def _fill(con, vid, day, pcts):
    """A tank log: one reading a day, the rise in the middle being the refuel."""
    for i, pct in enumerate(pcts):
        con.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, fuel_level_pct, fuel_liters) "
            "VALUES (?, ?, ?, ?)",
            (vid, f"2026-0{day}-{i+10:02d}T09:00:00+00:00", pct, pct / 2.0))
    con.commit()


def test_a_second_car_still_sees_its_own_older_refuels(tmp_path, monkeypatch):
    """Car one is scanned first and its readings are the most recent, so the shared mark lands
    beyond everything car two ever logged. Car two's refuel is in June; the mark says July."""
    path, con = _db(tmp_path, monkeypatch)
    _fill(con, 2, 6, [80.0, 20.0, 19.0, 95.0, 94.0, 93.0])   # car two: a fill in June
    _fill(con, 1, 7, [50.0, 49.0, 48.0, 47.0])               # car one: no fill, later readings
    con.close()

    db_reader.scan_fuel_refuels(1)          # moves the mark to car one's last-but-one reading
    found = db_reader.scan_fuel_refuels(2)  # car two's turn — its history is entirely behind it

    assert found == 1, "car two's June refuel must still be found after car one has scanned"


def test_the_scan_stays_incremental_for_the_car_that_ran_it(tmp_path, monkeypatch):
    """The watermark must keep doing its job: a second scan of the same car, with nothing new in
    the log, finds nothing again — and does not re-file the refuel it already filed."""
    path, con = _db(tmp_path, monkeypatch)
    _fill(con, 2, 6, [80.0, 20.0, 19.0, 95.0, 94.0, 93.0])
    con.close()

    first = db_reader.scan_fuel_refuels(2)
    second = db_reader.scan_fuel_refuels(2)

    assert first == 1 and second == 0, f"found {first} then {second}"
