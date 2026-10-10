"""A trip says where it started and ended: "A → B" on its row, a line for each end on its page, and a
search finds it by either place.

The name is worked out when the trip is read (db_reader.trip_places), from the address stored for the
spot (place_lookup) and the charging places: a charging place whose radius holds the point names it,
then the place's own name, then "road number" and the town. An address from OpenStreetMap is credited
once on every view showing one. The trip page has no 🧭 for the note any more: the note is the user's.
"""
import csv
import io
import re
import urllib.error
from datetime import datetime, timedelta, timezone
from html import unescape

import db as D
import db_reader
import geohash
import place_lookup
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")

DAY = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)
HOME, WORK, SHOP = (45.0700, 7.6800), (45.0800, 7.6900), (45.0900, 7.7000)
CREDIT = "openstreetmap.org/copyright"


@pytest.fixture
def car(tmp_path, monkeypatch):
    import main
    from starlette.testclient import TestClient
    db = D.Database(str(tmp_path / "t.db"))
    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(place_lookup, "_asked_at", float("-inf"))     # no request made a moment ago
    db.vid = db.ensure_vehicle("VINPLACES00000001", "B10")
    for key, value in (("setup_complete", "1"), ("timezone", "UTC"), ("language", "en")):
        db.set_setting(key, value)
    db_reader._lang_memo[0] = None
    db.client = TestClient(main.app)
    return db


def trip(db, start, end, at=DAY, minutes=20, note=None, soc=(80, 78)):
    cur = db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon, distance_km,"
        " duration_min, start_soc, end_soc, note) VALUES (?, ?, ?, ?, ?, ?, ?, 5.0, ?, ?, ?, ?)",
        (db.vid, at.isoformat(), (at + timedelta(minutes=minutes)).isoformat(), *(start or (None, None)),
         *(end or (None, None)), minutes, *soc, note))
    db._conn.commit()
    return cur.lastrowid


def address(point, provider="nominatim", **parts):
    place_lookup._store(geohash.encode(*point, 8), *point, provider, DAY, "found", parts)


def charging_place(db, point, name, radius_m=100):
    db._conn.execute("INSERT INTO charging_places (vehicle_id, name, latitude, longitude, radius_m, rate, enabled)"
                     " VALUES (?, ?, ?, ?, ?, 0.2, 1)", (db.vid, name, *point, radius_m))
    db._conn.commit()


def drawer(db, day=DAY, **q):
    query = "&".join(f"{k}={v}" for k, v in q.items())
    return db.client.get(f"/api/trips/calendar/day?year={day.year}&month={day.month}&day={day.day}&{query}").text


def lines(html):
    """Each row's "from → to" as the computer shows it (the phone's copy, `sm:hidden`, is the same line)."""
    out = []
    for attrs, inner in re.findall(r"<div([^>]*\bdata-trip-places\b[^>]*)>(.*?)</div>", html, re.DOTALL):
        if "hidden" in re.search(r'class="([^"]*)"', attrs).group(1).split():
            start, end = re.split(r"<span[^>]*>→</span>", inner)
            out.append((re.sub(r"<[^>]+>", "", start), re.sub(r"<[^>]+>", "", end)))
    return out


def page_lines(html):
    """The trip page's line for each end: (end, its name, "—" while it has none), without its ⓘ and 🧭."""
    out = []
    for end, inner in re.findall(r'<div[^>]*\bdata-trip-place="(start|end)"[^>]*>(.*?)</div>', html, re.DOTALL):
        text = re.sub(r"<[^>]+>", "", re.sub(r"<button.*?</button>", "", inner, flags=re.DOTALL)).replace("ⓘ", "").strip()
        out.append((end, "", "—") if text == "—" else (end, text, ""))
    return out


@pytest.mark.parametrize("parts, label", [
    ({"name": "Caffè Aurora", "road": "Via dei Mille", "house_number": "12", "locality": "Torino"}, "Caffè Aurora, Torino"),
    ({"road": "Via dei Mille", "house_number": "12", "locality": "Torino", "country_code": "it"}, "Via dei Mille 12, Torino"),
    ({"road": "Rue de la Paix", "house_number": "12", "locality": "Paris", "country_code": "fr"}, "12 Rue de la Paix, Paris"),
    ({"road": "Strada del Campo", "locality": "Torino"}, "Strada del Campo, Torino"),
    ({"suburb": "Borgo Nuovo", "locality": "Torino"}, "Borgo Nuovo, Torino"),
    ({"suburb": "Moncalieri", "locality": "Moncalieri"}, "Moncalieri"),
    ({"locality": "Torino"}, "Torino"),
    ({"display_name": "Località Sconosciuta, Piemonte, Italia"}, "Località Sconosciuta"),
])
def test_the_row_names_the_place_from_its_address(car, parts, label):
    address(WORK, **parts)
    address(HOME, road="Via Roma", locality="Torino")
    trip(car, HOME, WORK)
    assert lines(drawer(car)) == [("Via Roma, Torino", label)]


def test_a_charging_place_names_its_spot_before_the_address(car):
    address(HOME, road="Via Roma", locality="Torino")
    address(WORK, road="Corso Francia", locality="Torino")
    charging_place(car, (HOME[0] + 0.0003, HOME[1]), "Home")                 # about 33 m away
    tid = trip(car, HOME, WORK)
    assert lines(drawer(car)) == [("Home (charging place)", "Corso Francia, Torino")]
    assert page_lines(car.client.get(f"/trips/{tid}").text) == [("start", "Home (charging place)", ""),
                                                                 ("end", "Corso Francia, Torino", "")]


def test_a_merged_trip_goes_from_its_first_start_to_its_last_end(car):
    address(HOME, road="Via Roma", locality="Torino")
    address(WORK, road="Corso Francia", locality="Torino")
    address(SHOP, name="Mercato", locality="Torino")
    first = trip(car, HOME, SHOP)
    second = trip(car, SHOP, WORK, at=DAY + timedelta(minutes=23), soc=(78, 76))
    assert db_reader.merge_trips(first, second)["ok"]
    assert lines(drawer(car)) == [("Via Roma, Torino", "Corso Francia, Torino")]
    for tid in (first, second):                              # the page opened from either piece
        assert page_lines(car.client.get(f"/trips/{tid}").text) == [("start", "Via Roma, Torino", ""),
                                                                     ("end", "Corso Francia, Torino", "")]


def test_without_a_fix_or_an_address_there_is_no_line(car):
    address(HOME, road="Via Roma", locality="Torino")
    no_fix = trip(car, None, None, at=DAY)
    unknown = trip(car, WORK, SHOP, at=DAY + timedelta(hours=1))
    half = trip(car, HOME, SHOP, at=DAY + timedelta(hours=2))
    no_start = trip(car, (0.0, 0.0), HOME, at=DAY + timedelta(hours=3))
    html = drawer(car)
    assert lines(html) == [("—", "Via Roma, Torino"), ("Via Roma, Torino", "—")]
    assert html.count("data-trip-places") == 4, "the rows with an address, once per screen width"
    assert "data-trip-place-lines" not in car.client.get(f"/trips/{no_fix}").text
    page = car.client.get(f"/trips/{unknown}").text
    assert page_lines(page) == [("start", "", "—"), ("end", "", "—")]
    assert page.count('data-tip="Mate looks up the address') == 1, "one ⓘ, by the first dash"
    assert page_lines(car.client.get(f"/trips/{half}").text) == [("start", "Via Roma, Torino", ""), ("end", "", "—")]
    page = car.client.get(f"/trips/{no_start}").text
    assert page_lines(page) == [("end", "Via Roma, Torino", "")], "an end without a fix has no line"
    assert "Mate looks up the address" not in page


def test_a_missing_address_is_looked_up_on_request(car, monkeypatch):
    """As the note's 🧭 did, whatever the switch says and however old the trip: only the end without an
    address is asked about, and its lines come back named, without the 🧭."""
    import geocode
    from test_the_places_of_recent_trips_are_looked_up import Providers, nominatim, spot
    providers = Providers()
    providers.answers[("nominatim.openstreetmap.org", spot(*WORK))] = nominatim("Corso Francia")
    monkeypatch.setattr(geocode, "_get", providers)
    car.set_setting("place_lookup", "0")
    address(HOME, road="Via Roma", locality="Torino")
    tid = trip(car, HOME, WORK)
    assert f'hx-post="api/trips/{tid}/places"' in car.client.get(f"/trips/{tid}").text
    redrawn = car.client.post(f"/api/trips/{tid}/places").text
    assert providers.calls() == [spot(*WORK)]
    assert page_lines(redrawn) == [("start", "Via Roma, Torino", ""), ("end", "Corso Francia, Torino", "")]
    assert "data-trip-place-look-up" not in redrawn


def _refused(code, reason, body):
    return urllib.error.HTTPError("https://provider/reverse", code, reason, {}, io.BytesIO(body))


@pytest.mark.parametrize("provider, host, answer, says", [
    pytest.param("", "nominatim.openstreetmap.org", {"error": "Unable to geocode"},
                 "Nominatim has no address here", id="nothing there"),
    pytest.param("", "nominatim.openstreetmap.org", TimeoutError("timed out"),
                 "Asking Nominatim failed: timed out", id="no answer in time"),
    pytest.param("", "nominatim.openstreetmap.org",
                 urllib.error.URLError(OSError(8, "nodename nor servname provided, or not known")),
                 "Asking Nominatim failed: [Errno 8] nodename nor servname provided, or not known", id="no network"),
    pytest.param("", "nominatim.openstreetmap.org", urllib.error.URLError(ConnectionResetError()),
                 "Asking Nominatim failed: ConnectionResetError", id="a network error without words"),
    pytest.param("", "nominatim.openstreetmap.org", _refused(403, "Forbidden", b"<html>Access blocked</html>"),
                 "Asking Nominatim failed: HTTP 403 Forbidden", id="refused with a page"),
    pytest.param("geoapify", "api.geoapify.com",
                 _refused(401, "Unauthorized", b'{"statusCode": 401, "error": "Unauthorized", "message": "Invalid apiKey"}'),
                 "Asking Geoapify failed: HTTP 401 Unauthorized: Invalid apiKey", id="refused with a reason"),
])
def test_a_lookup_on_request_that_brings_no_address_says_why(car, monkeypatch, provider, host, answer, says):
    """Else the dash stays as if the 🧭 had done nothing: the provider has nothing there, or what went wrong, in
    the words of the network or of the provider."""
    import geocode
    from test_the_places_of_recent_trips_are_looked_up import Providers, spot
    providers = Providers()
    providers.answers[(host, spot(*WORK))] = answer
    monkeypatch.setattr(geocode, "_get", providers)
    if provider:
        car.set_setting("geocoder_provider", provider)
        db_reader.set_secret("geocoder_key", "the-key")
    address(HOME, road="Via Roma", locality="Torino")
    tid = trip(car, HOME, WORK)
    redrawn = car.client.post(f"/api/trips/{tid}/places").text
    assert providers.calls() == [spot(*WORK)]
    said = re.findall(r"<span[^>]*data-place-lookup-said[^>]*>(.*?)</span>", redrawn, re.DOTALL)
    assert [unescape(s).strip() for s in said] == [f"⚠️ {says}"]
    assert "data-trip-place-look-up" in redrawn, "and it can be asked again"


def test_the_trip_page_has_no_compass_for_the_note_any_more(car):
    tid = trip(car, HOME, WORK)
    page = car.client.get(f"/trips/{tid}").text
    assert f"api/trips/{tid}/auto-note" not in page and "auto_note" not in page
    assert car.client.post(f"/api/trips/{tid}/auto-note").status_code in (404, 405)


# ── the credit ────────────────────────────────────────────────────────────────

def test_each_view_with_an_openstreetmap_address_credits_it_once(car):
    address(HOME, road="Via Roma", locality="Torino")
    address(WORK, road="Corso Francia", locality="Torino")
    one = trip(car, HOME, WORK)
    trip(car, WORK, HOME, at=DAY + timedelta(minutes=23), soc=(78, 76))      # a stop short enough to merge
    trip(car, HOME, WORK, at=DAY + timedelta(days=1))
    views = {"day": drawer(car), "range": drawer(car, to_day=DAY.day + 1), "merge": drawer(car, merge=1),
             "search": car.client.get("/api/trips/search?q=torino").text,
             "trip page without a route": car.client.get(f"/trips/{one}").text,
             "month opened on a day": car.client.get(f"/trips?highlight={one}").text}
    assert "🔗" in views["merge"] and "data-trip-places" in views["merge"]
    for name, html in views.items():
        assert html.count(CREDIT) == 1, name
        assert "© OpenStreetMap</a>" in html, name


def test_a_view_without_an_openstreetmap_address_has_no_credit(car):
    address(WORK, provider="geoapify", road="Corso Francia", locality="Torino")
    tid = trip(car, HOME, WORK)
    assert lines(drawer(car)) == [("—", "Corso Francia, Torino")]
    assert CREDIT not in drawer(car)
    page = car.client.get(f"/trips/{tid}").text
    assert "Corso Francia, Torino" in page and CREDIT not in page


# ── the search ────────────────────────────────────────────────────────────────

def test_the_search_finds_a_trip_by_its_town_or_its_charging_place(car):
    address(HOME, road="Via Roma", locality="Torino", display_name="Via Roma, Torino, Piemonte, Italia")
    address(WORK, road="Via Garibaldi", locality="Chieri", display_name="Via Garibaldi, Chieri, Piemonte, Italia")
    charging_place(car, HOME, "Home")
    to_work = trip(car, HOME, WORK)
    trip(car, HOME, SHOP, at=DAY + timedelta(hours=3), note="dentist")

    def found(q):
        return [int(i) for i in re.findall(r'data-trip-id="(\d+)"', car.client.get(f"/api/trips/search?q={q}").text)]
    assert found("chieri") == [to_work]
    assert sorted(found("home")) == sorted(found("piemonte")) == [to_work, to_work + 1]
    assert found("dentist") == [to_work + 1], "the note is still searched"


def test_the_csv_export_says_where_each_trip_started_and_ended(car):
    address(HOME, road="Via Roma", locality="Torino")
    address(WORK, road="Corso Francia", locality="Torino")
    trip(car, HOME, WORK)
    rows = list(csv.DictReader(io.StringIO(car.client.get("/api/export/trips.csv").text)))
    assert [(r["start_place"], r["end_place"]) for r in rows] == [("Via Roma, Torino", "Corso Francia, Torino")]
    assert not {"search_text", "start_place_osm", "end_place_osm", "start_place_charging"} & set(rows[0])


def test_the_trip_summary_opens_with_where_the_trip_started_and_ended(car):
    address(HOME, road="Via Roma", locality="Torino")
    page = car.client.get(f"/trips/{trip(car, HOME, WORK)}").text
    summary = page[page.index("Trip summary"):]
    assert summary.index("data-trip-place-lines") < summary.index("Distance")


def test_the_lookup_is_switched_in_the_address_lookup_card(car):
    def box():
        card = car.client.get("/settings").text.split('hx-post="api/settings/geocoder"')[1].split("</form>")[0]
        return re.search(r'<input[^>]*name="place_lookup"[^>]*>', card).group(0)
    assert "checked" in box()
    car.client.post("/api/settings/geocoder", data={"place_lookup_present": "1"})
    assert car.get_setting("place_lookup") == "0" and "checked" not in box()
    car.client.post("/api/settings/geocoder", data={"place_lookup_present": "1", "place_lookup": "1"})
    assert car.get_setting("place_lookup") == "1"
