"""The Overview map follows the car.

It was drawn once, when the page loaded. The status card beside it refreshes itself every 30 s, so
the page said "last seen 6 s ago" over a marker left wherever the car was when the page was opened —
on a drive, kilometres behind it — until the page was reloaded.

The marker now comes from ONE builder, `main._last_position`: the page's first paint embeds it, and
the map polls it at /api/last-position. It reads what the poller already stored and tells the map
how often to ask — the poller's driving cadence, whatever the car is doing, because the web cannot
see the poller's real schedule. Nothing here reaches Leapmotor's servers.
"""
import datetime
import json
import re

import db as D
import db_reader
import pytest

HOUR_MS = 3600 * 1000


@pytest.fixture
def mate(tmp_path, monkeypatch):
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    import main
    from starlette.testclient import TestClient
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','B10')")
    pdb._conn.commit()
    db_reader.set_setting("setup_complete", "1")     # or "/" is the setup wizard, not the Overview
    monkeypatch.setattr(db_reader, "_lang_memo", [None])
    return pdb, TestClient(main.app)


def _position(pdb, lat, lon, *, driving=False, frame_age_ms=2 * HOUR_MS):
    """A row the poller would write. The frame is hours old by default, so the popup's "…ago"
    cannot tick between two reads in one test."""
    now = datetime.datetime.now(datetime.timezone.utc)
    pdb._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, gear, speed_kmh,"
        " frame_ts) VALUES (1,?,?,?,?,?,?)",
        (now.isoformat(), lat, lon, "D" if driving else "P", 50 if driving else 0,
         int(now.timestamp() * 1000) - frame_age_ms))
    pdb._conn.commit()


def _served(client):
    r = client.get("/api/last-position")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return r.json()


def _drawn(html):
    """The payload the page hands the map on first paint."""
    return json.loads(re.search(r"show\((\{.*?\})\);", html).group(1))


def test_the_map_is_told_where_the_car_is_now(mate):
    pdb, client = mate
    _position(pdb, 52.40, 16.90, driving=True)
    assert (_served(client)["lat"], _served(client)["lon"]) == (52.40, 16.90)
    _position(pdb, 52.45, 16.95, driving=True)
    assert (_served(client)["lat"], _served(client)["lon"]) == (52.45, 16.95)


@pytest.mark.parametrize("driving", [True, False])
def test_it_asks_at_the_pollers_driving_cadence_whatever_the_car_is_doing(mate, driving):
    """Parked with a 120 s interval, a car about to drive or on V2L polls at the driving pace and a
    boost every 10 s — the web cannot see either — so waiting out the parked interval could leave
    the marker two minutes behind a car already moving."""
    pdb, client = mate
    db_reader.set_setting("poll_driving", "20")
    db_reader.set_setting("poll_parked", "120")
    _position(pdb, 52.40, 16.90, driving=driving)
    assert _served(client)["refresh_s"] == 20


def test_before_any_position_it_says_so_and_still_when_to_ask(mate):
    _, client = mate
    assert _served(client) == {"lat": None, "lon": None, "label": None,
                               "refresh_s": db_reader.POLL_DRIVING_DEFAULT_S}


def test_a_first_poll_without_a_fix_is_not_a_position(mate):
    """(0, 0) is what a poll without a fix stores; with no earlier fix to fall back on, it is no
    position — not a car in the Gulf of Guinea. The next real fix draws the map."""
    pdb, client = mate
    _position(pdb, 0.0, 0.0)
    assert _served(client)["lat"] is None
    assert '<div id="map-box" style="display:none">' in client.get("/").text
    _position(pdb, 52.40, 16.90)
    assert (_served(client)["lat"], _served(client)["lon"]) == (52.40, 16.90)


def test_a_poll_without_a_fix_keeps_the_last_real_one(mate):
    pdb, client = mate
    _position(pdb, 52.40, 16.90)
    _position(pdb, 0.0, 0.0)
    assert (_served(client)["lat"], _served(client)["lon"]) == (52.40, 16.90)


def test_a_car_on_the_prime_meridian_is_drawn(mate):
    pdb, client = mate
    _position(pdb, 51.48, 0.0)
    assert (_served(client)["lat"], _served(client)["lon"]) == (51.48, 0.0)


def test_the_refresh_button_moves_the_marker(mate):
    """Refresh stores a row from the car's live signals and reloads the page; the map's first
    paint and its next poll both read that row."""
    pdb, client = mate
    _position(pdb, 52.40, 16.90)
    db_reader.save_fresh_signals({"3": "52.5", "2": "17.1"})
    assert (_served(client)["lat"], _served(client)["lon"]) == (52.5, 17.1)
    assert (_drawn(client.get("/").text)["lat"], _drawn(client.get("/").text)["lon"]) == (52.5, 17.1)


def test_the_page_draws_the_same_marker_the_map_is_served(mate):
    pdb, client = mate
    _position(pdb, 52.40, 16.90)
    assert _drawn(client.get("/").text) == _served(client)
    assert _served(client)["label"] == "B10 — 2h ago"


def test_with_no_position_the_placeholder_shows_and_the_map_waits_for_one(mate):
    """The map is created by the first position that arrives, so a fresh install needs no reload."""
    _, client = mate
    html = client.get("/").text
    assert '<div id="map-box" style="display:none">' in html
    assert '<div id="map-empty">' in html
    assert _drawn(html)["lat"] is None


def test_the_popup_speaks_the_readers_language(mate):
    pdb, client = mate
    db_reader.set_setting("language", "pl")
    _position(pdb, 52.40, 16.90)
    assert _served(client)["label"] == "B10 — 2 godz. temu"


def test_a_car_name_cannot_break_out_of_the_page(mate):
    """The name is data from the car's profile; the payload sits inside a <script>."""
    pdb, client = mate
    pdb._conn.execute("UPDATE vehicles SET car_type='</script><b>x</b>' WHERE id=1")
    pdb._conn.commit()
    _position(pdb, 52.40, 16.90)
    html = client.get("/").text
    assert "</script><b>x</b>" not in html
    assert _drawn(html)["label"].startswith("</script><b>x</b> — ")
