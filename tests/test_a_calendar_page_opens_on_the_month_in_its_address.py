"""A calendar page opens on the month its address names (?month=YYYY-MM), which the page keeps there so a
reload draws the month being looked at; without one, or with one that is not a month, it opens on today's,
which it marks as no month in particular.
A ?highlight= link names one trip or charge and opens on that one's month instead. The block's own load
asks for the same month, or it would swap today's back in.
"""
import re

import db as D
import db_reader
import pytest

pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

from test_a_days_heading_sums_up_its_trips import _client

_PAGES = ["trips", "charges", "wallbox", "fuel"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    import ha_client
    import main
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    for key, value in (("setup_complete", "1"), ("timezone", "UTC"), ("wallbox_enabled", "1"), ("is_reev", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min)"
              " VALUES (1,1,'2026-03-05T08:00:00+00:00','2026-03-05T08:30:00+00:00',12,30)")
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh)"
              " VALUES (1,1,'2026-03-05T20:00:00+00:00','2026-03-05T22:00:00+00:00',9)")
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    monkeypatch.setattr(ha_client, "is_configured", lambda: True)     # Wallbox draws its calendar only then
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {"power": "sensor.wallbox_power"})
    monkeypatch.setattr(main, "_fuel_blocked", lambda: False)
    return _client()


def _opens_on(client, page, **params):
    """The month the page draws ("" for today's), and the month its calendar block's own load asks for."""
    html = client.get(f"/{page}", params=params).text
    drawn = re.search(r'data-cal-month="([\d-]*)"', html)
    load = re.search(r'id="[a-z]+-calendar(?:-month)?-wrap"\s+hx-get="[^"]*\?year=(\d+)&month=(\d+)', html)
    assert drawn and load, f"/{page} has no calendar"
    return drawn[1], f"{load[1]}-{int(load[2]):02d}"


@pytest.mark.parametrize("page", _PAGES)
def test_the_page_opens_on_the_month_in_its_address(client, page):
    assert _opens_on(client, page, month="2026-07") == ("2026-07", "2026-07")


@pytest.mark.parametrize("page", _PAGES)
def test_without_a_month_the_page_opens_on_today(client, page):
    assert _opens_on(client, page) == ("", db_reader.today_local().strftime("%Y-%m"))


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "0000-05", "July", "2026-07x"])
def test_a_month_that_is_not_one_opens_today(client, month):
    assert _opens_on(client, "trips", month=month) == ("", db_reader.today_local().strftime("%Y-%m"))


@pytest.mark.parametrize("page", ["trips", "charges"])
def test_a_highlight_link_opens_on_its_own_month(client, page):
    assert _opens_on(client, page, month="2026-07", highlight=1) == ("2026-03", "2026-03")
