"""«Quando vado sull'auto secondaria, tutto quello che vedo in TUTTE le pagine dev'essere SOLO
dell'auto secondaria.» — Silvio, 08/08/26. Assoluto, e quindi verificato una funzione alla volta.

Un controllo a tappeto su `web/db_reader.py` — 104 funzioni che leggono una tabella per-auto — ne ha
trovate 26 senza lo scope. Le più sono scritture per `id`, che agiscono sulla riga cliccata da un
elenco già filtrato. Ma cinque erano perdite vere, e questo file le blocca.
→ [[feedback-gate-a-feature-find-every-copy]]
"""
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
A, B = "LFZT03AAAAAAAAAA1", "LFZC10BBBBBBBBBB2"


@pytest.fixture
def two_cars(tmp_path, monkeypatch):
    path = str(tmp_path / "p.db")
    database = D.Database(path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'T03')", (A,))
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,?,'C10')", (B,))
    database._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return database


def _signals(database, vehicle_id, pairs, ts=1_000):
    for k, v in pairs:
        database._conn.execute(
            "INSERT INTO raw_signals_log (vehicle_id, ts, sig_key, value) VALUES (?,?,?,?)",
            (vehicle_id, ts, k, v))
        ts += 1
    database._conn.commit()


# ── the research/REEV dashboard read the OTHER car's signals ──────────────────

def test_the_signal_count_is_this_cars_signals(two_cars):
    _signals(two_cars, 1, [("3260", "10"), ("3263", "20")])
    _signals(two_cars, 2, [("3260", "99")])
    db_reader.set_active_vehicle(A)
    assert db_reader.count_raw_signals() == 2
    db_reader.set_active_vehicle(B)
    assert db_reader.count_raw_signals() == 1


def test_the_latest_signals_never_come_from_the_other_car(two_cars):
    """🔴 The worst of the five. `MAX(id) GROUP BY sig_key` over both cars means the newest row
    wins — so the REEV dashboard could render the fuel level of the car you are NOT looking at, as
    though it were this one's."""
    _signals(two_cars, 1, [("3260", "AAA")], ts=1_000)
    _signals(two_cars, 2, [("3260", "BBB")], ts=2_000)   # newer, other car
    db_reader.set_active_vehicle(A)
    assert db_reader.latest_raw_signals().get("3260") == "AAA"
    db_reader.set_active_vehicle(B)
    assert db_reader.latest_raw_signals().get("3260") == "BBB"


def test_the_signal_export_carries_one_car(two_cars):
    _signals(two_cars, 1, [("a", "1"), ("b", "2")])
    _signals(two_cars, 2, [("c", "3")])
    db_reader.set_active_vehicle(A)
    assert [r[1] for r in db_reader.get_raw_signal_rows()] == ["a", "b"]


# ── a charge typed by hand belongs to the car you are looking at ──────────────

def test_a_manual_charge_is_filed_under_the_selected_car(two_cars):
    """🔴 It used to be `SELECT id FROM vehicles ORDER BY id LIMIT 1` — the FIRST car, always. Switch
    to the second, type in a charge from before Mate existed, and it lands on the other car's
    history: wrong totals on both, and nothing on screen to say so."""
    db_reader.set_active_vehicle(B)
    cid = db_reader.add_manual_charge("2026-07-01T10:00:00+00:00", 12.0, cost=3.0)
    row = two_cars._conn.execute("SELECT vehicle_id FROM charges WHERE id = ?", (cid,)).fetchone()
    assert row[0] == 2, "the charge belongs to the car that was on screen"


# ── two cars' trips must never be merged into one ─────────────────────────────

def test_trips_of_two_different_cars_cannot_be_merged(two_cars):
    """A merge takes two ids and nothing checked they were the same car. Merging car A's drive into
    car B's would put one car's kilometres and energy inside the other's trip — and the merge marker
    is the only thing written, so it looks tidy while being wrong."""
    for vid, start in ((1, "2026-07-01T10:00:00+00:00"), (2, "2026-07-01T10:30:00+00:00")):
        two_cars._conn.execute(
            "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km) VALUES (?,?,?,?)",
            (vid, start, start.replace("10:", "10:2"), 10.0))
    two_cars._conn.commit()
    out = db_reader.merge_trips(1, 2)
    assert out.get("ok") is False, "a cross-car merge must be refused"
    assert "car" in str(out.get("error", "")).lower()


def test_two_trips_of_the_same_car_still_merge(two_cars):
    """The guard must not break the feature it protects."""
    for start, end in (("2026-07-01T10:00:00+00:00", "2026-07-01T10:20:00+00:00"),
                       ("2026-07-01T10:22:00+00:00", "2026-07-01T10:45:00+00:00")):
        two_cars._conn.execute(
            "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km) VALUES (1,?,?,5.0)",
            (start, end))
    two_cars._conn.commit()
    assert db_reader.merge_trips(1, 2).get("ok") is True


# ── two facts the poller LEARNS from a car, kept in one key for both ───────────

def test_the_charge_schedule_shown_is_this_cars(two_cars):
    """The poller reads each car's plan from the cloud and stores it. One key meant the car polled
    last overwrote the other, and the Scheduling page then showed one car's window under both."""
    two_cars.set_charge_schedule(A, enabled=True, start="23:00", end="07:00")
    two_cars.set_charge_schedule(B, enabled=True, start="01:00", end="05:00")
    db_reader.set_active_vehicle(A)
    assert db_reader.get_charge_schedule_window()["start"] == "23:00"
    db_reader.set_active_vehicle(B)
    assert db_reader.get_charge_schedule_window()["start"] == "01:00"


def test_the_remembered_gps_sign_is_per_car(two_cars):
    """🔴 This is the defect that has come back FIVE times — a car plotted in the sea because the
    hemisphere was guessed. The sign is LEARNED from one car's own history, and it is a firmware
    quirk, so two cars can legitimately differ. One key for both means the second car's learning
    silently overwrites the first's, and that car goes back into the Gulf of Guinea.
    → [[car-plotted-in-the-sea-longitude-sign]]"""
    two_cars.set_gps_signs(A, lat="positive", lon="negative")
    two_cars.set_gps_signs(B, lat="positive", lon="positive")
    assert two_cars.get_gps_signs(A) == {"lat": "positive", "lon": "negative"}
    assert two_cars.get_gps_signs(B) == {"lat": "positive", "lon": "positive"}


def test_a_car_that_has_learnt_nothing_falls_back_to_the_shared_sign(two_cars):
    """Every install today has the shared keys and no per-car ones — they must keep working, or a
    single-car owner's map moves on update."""
    two_cars.set_setting("gps_lat_sign", "negative")
    two_cars.set_setting("gps_lon_sign", "negative")
    assert two_cars.get_gps_signs(A) == {"lat": "negative", "lon": "negative"}
