"""The addresses of where recent trips started and ended are looked up once per spot, in the background.

place_lookup.sweep asks the provider chosen in Settings ▸ Address lookup, and only it, about the ends of
trips that ended in the last three days: once per geohash-8 cell, at most four requests a pass, 1.1 s
apart, and only while that card's switch is on. A failure waits and is asked again at the same provider;
"nothing here" is final for the provider that said it. Every request goes through geocode._get, replaced
here by the providers' answers, so the choice of provider and the mapping of its answer are the real ones.
"""
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import db as D
import db_reader
import geocode
import geohash
import place_lookup
import pytest

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def spot(lat, lon):
    """The centre of the geohash-8 cell holding (lat, lon): a point a few metres off stays in it."""
    (la0, la1), (lo0, lo1) = geohash._bounds(geohash.encode(lat, lon, 8))
    return round((la0 + la1) / 2, 7), round((lo0 + lo1) / 2, 7)


HOME, WORK, SHOP, PARK, FIELD = (spot(45.0700 + 0.002 * i, 7.6800) for i in range(5))
NEAR_HOME = (HOME[0] + 0.000005, HOME[1] + 0.000005)          # under a metre away: the same cell


def nominatim(road, town="Torino"):
    return {"display_name": f"{road}, {town}, Italia", "address": {"road": road, "city": town, "country_code": "it"}}


class Providers:
    """Answers per spot and host; an Exception answer is raised, as a timeout would be."""

    def __init__(self):
        self.answers, self.asked = {}, []

    def __call__(self, url):
        u = urlparse(url)
        q = parse_qs(u.query)
        if u.hostname == "api.tomtom.com":
            lat, lon = u.path.rsplit("/", 1)[1].removesuffix(".json").split(",")
        else:
            lat, lon = q["lat"][0], q["lon"][0]
        point = spot(float(lat), float(lon))          # the providers answer for the spot, as the sweep asks
        self.asked.append((u.hostname, point))
        answer = self.answers.get((u.hostname, point), {"error": "Unable to geocode"})
        if isinstance(answer, Exception):
            raise answer
        return answer

    def calls(self, host=None):
        return [p for h, p in self.asked if host is None or h == host]


@pytest.fixture
def car(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(place_lookup, "_asked_at", float("-inf"))     # no request made a moment ago
    db.vid = db.ensure_vehicle("VINPLACES00000001", "B10")
    db.providers = Providers()
    monkeypatch.setattr(geocode, "_get", db.providers)
    return db


def trip(db, start, end, ended_ago=timedelta(hours=1), vid=None):
    ended = NOW - ended_ago
    cur = db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon, distance_km)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 5.0)",
        (vid or db.vid, (ended - timedelta(minutes=20)).isoformat(), ended.isoformat(), *start, *end))
    db._conn.commit()
    return cur.lastrowid


def sweep(now=NOW):
    pauses = []
    n = place_lookup.sweep(now=now, pause=pauses.append)
    n["pauses"] = pauses
    return n


def rows(db):
    return {r["geohash"]: dict(r) for r in db._conn.execute("SELECT * FROM addresses")}


def row(db, point):
    return rows(db).get(geohash.encode(*point, 8))


def places(trip_id):
    t = dict(db_reader._get().execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone())
    db_reader.trip_places([t])
    return t["start_place"], t["end_place"]


def test_a_sweep_stores_the_address_of_both_ends(car):
    car.providers.answers = {("nominatim.openstreetmap.org", HOME): nominatim("Via Roma"),
                             ("nominatim.openstreetmap.org", WORK): nominatim("Corso Francia")}
    tid = trip(car, HOME, WORK)
    n = sweep()
    assert (n["calls"], n["found"], n["pauses"]) == (2, 2, [1.1])
    assert row(car, WORK)["provider"] == "nominatim" and row(car, WORK)["road"] == "Corso Francia"
    assert places(tid) == ("Via Roma, Torino", "Corso Francia, Torino")


def test_a_second_trip_to_the_same_spot_asks_nobody(car):
    car.providers.answers = {("nominatim.openstreetmap.org", HOME): nominatim("Via Roma"),
                             ("nominatim.openstreetmap.org", WORK): nominatim("Corso Francia")}
    trip(car, HOME, WORK, ended_ago=timedelta(hours=5))
    sweep()
    tid = trip(car, WORK, NEAR_HOME)
    n = sweep()
    assert (n["calls"], n["known"]) == (0, 2)
    assert len(car.providers.calls()) == 2
    assert places(tid) == ("Corso Francia, Torino", "Via Roma, Torino")


def test_failing_spots_wait_and_do_not_hold_up_an_older_one(car):
    """The four newest ends fail; each failure ends its pass, and the spot that failed waits without
    taking the place of the ones behind it, so the fifth pass reaches the oldest end."""
    for i, p in enumerate((FIELD, PARK, SHOP, WORK)):
        car.providers.answers[("nominatim.openstreetmap.org", p)] = OSError("timed out")
        trip(car, p, p, ended_ago=timedelta(minutes=10 * (i + 1)))
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Roma")
    trip(car, HOME, HOME, ended_ago=timedelta(hours=2))
    for _ in range(4):
        assert sweep()["failed"] == 1
    assert sweep()["found"] == 1 and row(car, HOME)["status"] == "found"
    failed = row(car, FIELD)
    assert (failed["status"], failed["attempts"]) == ("failed", 1)
    assert failed["retry_at"] == (NOW + timedelta(minutes=15)).isoformat()
    assert sweep()["calls"] == 0, "every failed spot is still waiting"
    car.providers.answers[("nominatim.openstreetmap.org", FIELD)] = nominatim("Strada del Campo")
    assert sweep(NOW + timedelta(minutes=15))["found"] == 1
    assert row(car, FIELD)["status"] == "found"


def test_a_spot_that_fails_again_waits_twice_as_long_up_to_six_hours(car):
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = OSError("HTTP Error 503")
    trip(car, HOME, HOME)
    now, waits = NOW, []
    for _ in range(7):
        sweep(now)
        retry = datetime.fromisoformat(row(car, HOME)["retry_at"])
        waits.append((retry - now) / timedelta(minutes=1))
        now = retry
    assert waits == [15, 30, 60, 120, 240, 360, 360]


def test_nothing_there_is_not_asked_again_of_the_same_provider(car):
    trip(car, HOME, HOME)
    assert sweep()["none"] == 1
    assert row(car, HOME)["status"] == "none"
    assert sweep()["calls"] == 0 and len(car.providers.calls()) == 1


def test_another_provider_is_asked_where_the_first_had_nothing(car):
    car.set_setting("geocoder_provider", "geoapify")
    car.set_secret("geocoder_key", "k")
    car.providers.answers[("api.geoapify.com", HOME)] = {"features": []}
    old = trip(car, HOME, HOME, ended_ago=timedelta(hours=3))
    assert sweep()["none"] == 1 and row(car, HOME)["provider"] == "geoapify"
    car.set_setting("geocoder_provider", "")
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Roma")
    new = trip(car, HOME, NEAR_HOME)
    assert sweep()["found"] == 1
    assert row(car, HOME)["provider"] == "nominatim"
    assert places(new) == places(old) == ("Via Roma, Torino", "Via Roma, Torino")


def test_a_keyed_provider_that_fails_is_not_replaced_by_nominatim(car):
    car.set_setting("geocoder_provider", "geoapify")
    car.set_secret("geocoder_key", "k")
    car.providers.answers[("api.geoapify.com", HOME)] = OSError("timed out")
    trip(car, HOME, HOME)
    assert sweep()["failed"] == 1
    assert car.providers.calls("nominatim.openstreetmap.org") == []
    assert (row(car, HOME)["status"], row(car, HOME)["provider"]) == ("failed", "geoapify")


def test_a_spot_that_failed_is_asked_at_once_of_the_provider_chosen_next(car):
    """The wait belongs to the provider that failed; its failures do not count against the next one."""
    car.set_setting("geocoder_provider", "geoapify")
    car.set_secret("geocoder_key", "k")
    car.providers.answers[("api.geoapify.com", HOME)] = OSError("timed out")
    trip(car, HOME, HOME)
    sweep()
    sweep(NOW + timedelta(minutes=20))                  # the second failure in a row: half an hour's wait
    assert row(car, HOME)["attempts"] == 2
    car.set_setting("geocoder_provider", "")
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = OSError("timed out")
    assert sweep(NOW + timedelta(minutes=21))["calls"] == 1
    assert (row(car, HOME)["provider"], row(car, HOME)["attempts"]) == ("nominatim", 1)


def test_a_provider_chosen_later_does_not_ask_again_where_an_address_was_found(car):
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Roma")
    trip(car, HOME, HOME)
    assert sweep()["found"] == 1
    car.set_setting("geocoder_provider", "geoapify")
    car.set_secret("geocoder_key", "k")
    assert sweep(NOW + timedelta(minutes=1))["calls"] == 0


def test_an_address_is_asked_again_once_it_is_old(car):
    """Names change at the provider (a shop, a street): the next trip to the spot brings the new one, for
    every trip there."""
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Roma")
    old = trip(car, HOME, HOME)
    sweep()
    age = timedelta(days=place_lookup.ADDRESS_REFRESH_DAYS)
    new = trip(car, HOME, NEAR_HOME, ended_ago=timedelta(hours=1) - age)
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Garibaldi")
    assert sweep(NOW + age - timedelta(days=1))["calls"] == 0, "not before it is old"
    assert sweep(NOW + age)["found"] == 1
    assert places(old) == places(new) == ("Via Garibaldi, Torino", "Via Garibaldi, Torino")


def test_a_refresh_never_takes_an_address_away(car):
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Roma")
    tid = trip(car, HOME, HOME)
    sweep()
    age = timedelta(days=place_lookup.ADDRESS_REFRESH_DAYS)
    trip(car, HOME, HOME, ended_ago=timedelta(hours=1) - age)
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = OSError("timed out")
    assert sweep(NOW + age)["failed"] == 1
    assert places(tid)[0] == "Via Roma, Torino"
    assert sweep(NOW + age + timedelta(minutes=1))["calls"] == 0, "it waits as any failure does"
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = {"error": "Unable to geocode"}
    assert sweep(NOW + age + timedelta(hours=1))["none"] == 1
    assert places(tid)[0] == "Via Roma, Torino"
    assert sweep(NOW + age + timedelta(hours=2))["calls"] == 0, "nor is it asked again before it is old again"


def test_a_refresh_failing_at_another_provider_waits_longer_each_time(car):
    """The wait belongs to the provider that failed; the address stays credited to the one it came from."""
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Roma")
    tid = trip(car, HOME, HOME)
    sweep()
    age = timedelta(days=place_lookup.ADDRESS_REFRESH_DAYS)
    trip(car, HOME, HOME, ended_ago=timedelta(hours=1) - age)
    car.set_setting("geocoder_provider", "geoapify")
    car.set_secret("geocoder_key", "k")
    car.providers.answers[("api.geoapify.com", HOME)] = OSError("timed out")
    assert sweep(NOW + age)["failed"] == 1
    assert sweep(NOW + age + timedelta(minutes=15))["failed"] == 1
    assert sweep(NOW + age + timedelta(minutes=30))["calls"] == 0, "the second failure waits half an hour"
    assert (row(car, HOME)["provider"], places(tid)[0]) == ("nominatim", "Via Roma, Torino")


def test_a_provider_chosen_after_a_failed_refresh_is_asked_at_once(car):
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Roma")
    trip(car, HOME, HOME)
    sweep()
    age = timedelta(days=place_lookup.ADDRESS_REFRESH_DAYS)
    trip(car, HOME, HOME, ended_ago=timedelta(hours=1) - age)
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = OSError("timed out")
    assert sweep(NOW + age)["failed"] == 1
    car.set_setting("geocoder_provider", "geoapify")
    car.set_secret("geocoder_key", "k")
    assert sweep(NOW + age + timedelta(minutes=1))["calls"] == 1


def test_nothing_there_at_locationiq_is_not_a_failure(car):
    """LocationIQ says "Unable to geocode" with HTTP 404; any other error is still a failure."""
    car.set_setting("geocoder_provider", "locationiq")
    car.set_secret("geocoder_key", "k")
    car.providers.answers[("us1.locationiq.com", HOME)] = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    car.providers.answers[("us1.locationiq.com", WORK)] = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    trip(car, HOME, HOME, ended_ago=timedelta(hours=2))
    trip(car, WORK, WORK)
    assert sweep()["failed"] == 1 and row(car, WORK)["status"] == "failed"
    assert sweep()["none"] == 1 and row(car, HOME)["status"] == "none"


def test_a_keyed_provider_without_its_key_is_nominatim(car):
    car.set_setting("geocoder_provider", "tomtom")
    car.providers.answers[("nominatim.openstreetmap.org", HOME)] = nominatim("Via Roma")
    trip(car, HOME, HOME)
    sweep()
    assert row(car, HOME)["provider"] == "nominatim" and car.providers.calls("api.tomtom.com") == []


def test_a_trip_that_ended_four_days_ago_is_left_alone(car):
    car.set_setting("geocoder_provider", "geoapify")
    car.set_secret("geocoder_key", "k")
    place_lookup._store(geohash.encode(*HOME, 8), *HOME, "geoapify", NOW - timedelta(days=4), "failed",
                        attempts=1, retry_at=(NOW - timedelta(days=4)).isoformat())
    place_lookup._store(geohash.encode(*WORK, 8), *WORK, "nominatim", NOW - timedelta(days=4), "none")
    trip(car, HOME, WORK, ended_ago=timedelta(days=4))
    trip(car, SHOP, PARK, ended_ago=timedelta(days=3, minutes=1))
    n = sweep()
    assert (n["cells"], n["calls"]) == (0, 0)


def test_a_merged_trip_is_looked_up_while_its_last_piece_ended_recently(car):
    """A merged trip starts where its first piece did, which ended just before the window and its last just after."""
    for p, road in ((HOME, "Via Roma"), (SHOP, "Via Po"), (PARK, "Via Lagrange"), (WORK, "Corso Francia")):
        car.providers.answers[("nominatim.openstreetmap.org", p)] = nominatim(road)
    first = trip(car, HOME, SHOP, ended_ago=timedelta(days=3, minutes=1))
    last = trip(car, PARK, WORK, ended_ago=timedelta(days=3) - timedelta(minutes=21))   # set off 2 minutes later
    assert db_reader.merge_trips(first, last)["ok"]
    assert sweep()["calls"] == 4
    assert car.providers.calls()[:2] == [WORK, PARK], "the group is as new as its last piece"
    shown, _ = db_reader.trip_group(first)
    db_reader.trip_places([shown])
    assert (shown["start_place"], shown["end_place"]) == ("Via Roma, Torino", "Corso Francia, Torino")


def test_a_pass_makes_at_most_four_requests_for_every_car(car):
    """A second car's trips are looked up too, whichever car the panel shows."""
    other = car.ensure_vehicle("VINPLACES00000002", "C10")
    car.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "VINPLACES00000001")
    for p in (HOME, WORK, SHOP, PARK, FIELD):
        car.providers.answers[("nominatim.openstreetmap.org", p)] = nominatim(f"Via {p[0]}")
    trip(car, HOME, WORK, ended_ago=timedelta(hours=3))
    trip(car, SHOP, PARK, ended_ago=timedelta(hours=2), vid=other)
    trip(car, FIELD, FIELD, ended_ago=timedelta(hours=1), vid=other)
    n = sweep()
    assert (n["cells"], n["calls"], n["pauses"]) == (5, 4, [1.1] * 3)
    assert car.providers.calls()[:3] == [FIELD, PARK, SHOP], "newest end first"
    assert sweep()["calls"] == 1


def test_an_old_trip_gets_the_address_a_new_one_brought(car):
    car.providers.answers = {("nominatim.openstreetmap.org", HOME): nominatim("Via Roma"),
                             ("nominatim.openstreetmap.org", WORK): nominatim("Corso Francia")}
    old = trip(car, HOME, WORK, ended_ago=timedelta(days=40))
    assert places(old) == (None, None)
    trip(car, NEAR_HOME, WORK)
    assert sweep()["calls"] == 2
    assert places(old) == ("Via Roma, Torino", "Corso Francia, Torino")
    assert sweep()["calls"] == 0


def test_an_end_without_a_fix_is_not_asked_about(car):
    trip(car, (0.0, 0.0), (None, None))
    assert sweep()["cells"] == 0 and car.providers.calls() == []


def test_a_lookup_on_request_waits_a_second_after_the_pass(car, monkeypatch):
    """Nominatim's one request a second holds for the pass and the trip page's 🧭 together."""
    clock = [1000.0]
    monkeypatch.setattr(place_lookup, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time,
                                                              sleep=time.sleep))   # the module's own clock
    car.providers.answers = {("nominatim.openstreetmap.org", HOME): nominatim("Via Roma")}
    trip(car, HOME, HOME)
    assert sweep()["calls"] == 1
    clock[0] += 0.4
    pauses = []
    assert place_lookup.look_up_now([WORK, HOME], now=NOW, pause=pauses.append)["calls"] == 1, "HOME is known"
    assert pauses == [pytest.approx(0.7)]


def test_a_cell_found_while_the_compass_waited_is_not_asked_again(car):
    """The 🧭 waits while a pass asks, then reads the table: what the pass has just found costs no request."""
    car.providers.answers = {("nominatim.openstreetmap.org", HOME): nominatim("Via Roma")}
    compass = threading.Thread(target=place_lookup.look_up_now, args=([HOME],),
                               kwargs={"now": NOW, "pause": lambda s: None})
    with place_lookup._asking:                                  # a pass is asking
        compass.start()
        compass.join(0.2)                                       # the 🧭 is waiting for it
        place_lookup._store(geohash.encode(*HOME, 8), *HOME, "nominatim", NOW, "found", {"road": "Via Roma"})
    compass.join()
    assert car.providers.calls() == []


@pytest.fixture
def started(car, monkeypatch):
    """place_lookup's threading.Thread, recording what would have started."""
    runs = []

    class Started:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            runs.append(self.target)
    monkeypatch.setattr(place_lookup, "threading", SimpleNamespace(Thread=Started))   # its own, not the process's
    monkeypatch.setattr(place_lookup, "_running", False)
    return runs


def test_a_write_that_fails_does_not_stop_the_lookups(car, started, monkeypatch):
    """The poller can hold the database for longer than a write waits; the next minute tries again."""
    real = db_reader.set_setting

    def locked(key, value):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db_reader, "set_setting", locked)
    place_lookup.maybe_sweep()
    assert started == []
    monkeypatch.setattr(db_reader, "set_setting", real)
    place_lookup.maybe_sweep()
    assert started == [place_lookup._sweep_now]


def test_switched_off_no_pass_starts(car, started):
    trip(car, HOME, WORK)
    car.set_setting("place_lookup", "0")
    place_lookup.maybe_sweep()
    assert started == []


def test_the_switch_starts_as_the_automatic_note_was(tmp_path):
    """Until trips had addresses of their own, switching that note off kept their ends from the provider."""
    path = str(tmp_path / "before.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    old.execute("INSERT INTO settings VALUES ('auto_note', '0')")
    old.commit()
    old.close()
    assert D.Database(path).get_setting("place_lookup") == "0"
    D.Database(path).set_setting("auto_note", "1")
    assert D.Database(path).get_setting("place_lookup") == "0", "copied once, then a switch of its own"
    assert D.Database(str(tmp_path / "new.db")).get_setting("place_lookup") == "1"


def test_a_minute_passes_between_sweeps_whichever_way_the_clock_moved(car, started, monkeypatch):
    clock = [1_000_000.0]
    # The module's own `time`, not the process's; its background loop still sleeps for real.
    monkeypatch.setattr(place_lookup, "time", SimpleNamespace(time=lambda: clock[0], sleep=time.sleep))
    place_lookup.maybe_sweep()
    place_lookup._running = False                       # the sweep it started is over
    clock[0] += 30
    place_lookup.maybe_sweep()
    assert len(started) == 1, "half a minute later"
    clock[0] -= 3600
    place_lookup.maybe_sweep()
    assert len(started) == 2, "the host clock stepped back an hour"


def test_the_demo_names_its_trips_without_asking_anyone(tmp_path, monkeypatch):
    """The demo seeds a new database at every start: its addresses come with it, or each start would ask again."""
    path = str(tmp_path / "demo.db")
    seed = Path(__file__).resolve().parent.parent / "poller" / "seed_demo.py"
    subprocess.run([sys.executable, str(seed)], env={**os.environ, "DB_PATH": path}, check=True, capture_output=True)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(geocode, "_get", Providers())
    assert place_lookup.sweep(pause=lambda s: None)["calls"] == 0
    recent = [dict(r) for r in db_reader._get().execute("SELECT * FROM trips ORDER BY started_at DESC LIMIT 5")]
    db_reader.trip_places(recent)
    assert all(t["start_place"] and t["end_place"] for t in recent)
