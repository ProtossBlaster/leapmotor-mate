"""The kilometres measured out of contact say how many of them are no longer a mystery — #298.

@arzthilfe asked the right question and never got an answer. Of the eleven kilometres missing from
21 September, eight came back as trips imported from Leapmotor's own history — and the figure above
them, 33 km, did not move. Should it not be 25?

NO, AND THAT IS THE POINT. Those kilometres were covered while the cloud had nothing to say, which
is exactly what the figure counts, and the silence they sit in still cannot be divided into the end
of one drive, a stop and the start of another. Subtracting them would make the label false.

WHAT WAS MISSING is the rest of the sentence. The card now says how much of the silence the cloud's
own history has since accounted for — beside the measurement, not inside it. A record the cloud
returns with 0 km accounts for nothing and is not counted; a trip that crosses the edge of a window
is left out, because the window bounds the silence and not the drive, so nothing in it says how
much of that trip belongs inside.
"""
from datetime import datetime, timedelta, timezone

import db_reader
from events_fixture import Car, web

NOW = datetime(2026, 9, 21, 11, 0, tzinfo=timezone.utc)


def a_car(tmp_path, monkeypatch):
    car = Car(tmp_path, name="gaps.db")
    web(car, monkeypatch)
    return car


def gap(car, start, minutes, km, soc_from=60.0, soc_to=58.0):
    car.db._conn.execute(
        "INSERT INTO offline_gaps (vehicle_id, started_at, ended_at, distance_km, soc_start,"
        " soc_end, energy_kwh) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (car.vid, start.isoformat(), (start + timedelta(minutes=minutes)).isoformat(), km,
         soc_from, soc_to, round((soc_from - soc_to) / 100 * 65, 2)))


def cloud_trip(car, start, minutes, km):
    """A trip recovered from Leapmotor's history: a distance, and the link that says where it came
    from. No GPS, no odometer — it was never observed here."""
    car.db._conn.execute(
        "CREATE TABLE IF NOT EXISTS api_lab_cloud_trip_links (trip_id INTEGER)")
    trip_id = car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km) VALUES (?, ?, ?, ?)",
        (car.vid, start.isoformat(), (start + timedelta(minutes=minutes)).isoformat(),
         km)).lastrowid
    car.db._conn.execute("INSERT INTO api_lab_cloud_trip_links (trip_id) VALUES (?)", (trip_id,))
    return trip_id


def test_the_measurement_is_not_reduced_by_what_came_back(tmp_path, monkeypatch):
    """His 21 September, in the shape the bundle showed it: a 34-minute silence worth 11 km, with a
    7 km trip recovered inside it."""
    car = a_car(tmp_path, monkeypatch)
    gap(car, NOW, 34, 11.0)
    cloud_trip(car, NOW + timedelta(minutes=10), 10, 7.0)
    car.db._conn.commit()
    out = db_reader.offline_gaps_summary()
    assert out["total_km"] == 11.0
    assert out["recovered_km"] == 7.0


def test_with_nothing_recovered_the_figure_is_absent(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    gap(car, NOW, 34, 11.0)
    car.db._conn.commit()
    assert db_reader.offline_gaps_summary()["recovered_km"] == 0.0


def test_a_cloud_record_without_distance_accounts_for_nothing(tmp_path, monkeypatch):
    """The "cloud segments with 0 km" panel of his own installation: a record the cloud returns
    with no distance looks exactly like a drive that never happened."""
    car = a_car(tmp_path, monkeypatch)
    gap(car, NOW, 34, 11.0)
    cloud_trip(car, NOW + timedelta(minutes=10), 10, 0.0)
    car.db._conn.commit()
    assert db_reader.offline_gaps_summary()["recovered_km"] == 0.0


def test_a_trip_crossing_the_edge_is_left_out(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    gap(car, NOW, 30, 11.0)
    cloud_trip(car, NOW + timedelta(minutes=25), 20, 7.0)       # ends after the silence does
    car.db._conn.commit()
    assert db_reader.offline_gaps_summary()["recovered_km"] == 0.0


def test_a_trip_of_our_own_is_not_a_recovery(tmp_path, monkeypatch):
    """Only the cloud's history counts. A trip Mate recorded itself inside a window would mean the
    window was not silence at all, and it is not evidence of anything being given back."""
    car = a_car(tmp_path, monkeypatch)
    gap(car, NOW, 34, 11.0)
    car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km) VALUES (?, ?, ?, 7.0)",
        (car.vid, (NOW + timedelta(minutes=10)).isoformat(),
         (NOW + timedelta(minutes=20)).isoformat()))
    car.db._conn.commit()
    assert db_reader.offline_gaps_summary()["recovered_km"] == 0.0


def test_every_window_carries_its_own_figure(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    gap(car, NOW, 34, 11.0)
    cloud_trip(car, NOW + timedelta(minutes=10), 10, 7.0)
    gap(car, NOW + timedelta(days=4), 60, 21.0)
    car.db._conn.commit()
    out = db_reader.offline_gaps_summary()
    assert out["count"] == 2
    assert out["total_km"] == 32.0 and out["recovered_km"] == 7.0
    assert [w["recovered_km"] for w in out["windows"]] == [7.0, 0.0]


def test_the_two_cards_print_the_figure(tmp_path, monkeypatch):
    """Both places the figure appears, rendered. The branch is behind an `{% if %}`, so every other
    test on these pages renders them with it false — a mistake inside it would never be seen."""
    car = Car(tmp_path, name="gaps.db")
    client = web(car, monkeypatch)
    gap(car, NOW, 34, 11.0)
    cloud_trip(car, NOW + timedelta(minutes=10), 10, 7.0)
    car.db._conn.commit()

    # The apostrophe of "Leapmotor's" is escaped in the rendered HTML, so the match stops short
    # of it rather than spelling the entity out.
    printed = "of which 7 km have come back as trips in Leapmotor"
    stats = client.get("/statistics")
    assert stats.status_code == 200
    assert printed in stats.text

    month = client.get(f"/api/trips/calendar?year={NOW.year}&month={NOW.month}")
    assert month.status_code == 200
    assert printed in month.text


def test_an_installation_that_never_imported_anything_is_unaffected(tmp_path, monkeypatch):
    """No link table at all — the cloud history was never switched on. Nothing to join against,
    and nothing must raise."""
    car = a_car(tmp_path, monkeypatch)
    gap(car, NOW, 34, 11.0)
    car.db._conn.commit()
    out = db_reader.offline_gaps_summary()
    assert out["total_km"] == 11.0 and out["recovered_km"] == 0.0
