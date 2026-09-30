"""The trip chart leaves a speed the car did not send blank, as it does SoC, power and the rest.

Such a point is stored without a speed (tests/test_a_missing_speed_is_unknown_on_the_trips_points.py);
the chart drew it at 0 km/h, a stop in the middle of the line. Measured in a real browser, because
the chart is drawn by ApexCharts at run time. Skips where it cannot run (no playwright, no Chromium).
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

VIN = "LVIN0000000000001"
START = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
SPEEDS = [50, 60, None, 60, 0, 50]      # the third reading without a speed, the fifth a measured stop


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-trip-speed-gap")
    db = data / "leapmotor_mate.db"
    trip = ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
            " start_soc, end_soc) VALUES (1,1,?,?,5,5,80,79)")
    point = ("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
             " VALUES (1,?,?,9.0,?,?)")
    rows = [("INSERT INTO settings (key, value) VALUES ('elevation_enabled', '0')", ()),
            (trip, (START.isoformat(), (START + timedelta(minutes=5)).isoformat()))]
    rows += [(point, ((START + timedelta(minutes=k)).isoformat(), 45 + k * 0.01, kmh, 80 - k * 0.2))
             for k, kmh in enumerate(SPEEDS)]
    seed_database(db, VIN, rows)
    with served(data, db) as url:
        yield url


def test_the_speed_line_breaks_where_the_car_sent_no_speed(mate):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        assert page.goto(mate + "/trips/1").status == 200
        page.wait_for_function("() => document.getElementById('trip-profile') && document.getElementById('trip-profile')._c")
        line = page.evaluate("() => document.getElementById('trip-profile')._c.w.config.series"
                             ".filter(s => s.name === 'Speed')[0].data")
        assert [v is None for v in line] == [kmh is None for kmh in SPEEDS], \
            "a missing speed is drawn as a point of the line, a measured stop is not a gap"
    finally:
        browser.close()
        pw.stop()
