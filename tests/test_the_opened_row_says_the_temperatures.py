"""The chart under an opened charge row comes with a line above it that says what the chart's own readings
say: the battery's temperature at the first and the last reading, the outside temperature from its lowest
to its highest (each one figure when it did not change), and the average power over the charge. No reading,
no entry; no readings at all, no line. The line comes with the chart, from the same readings, so a day of
rows asks for nothing more.

Charge 1: an hour at 7 kW, the battery from 23 to 38 °C, the outside air 22 °C throughout.
Charge 2: the same readings without temperatures. Charge 3: no readings at all.
Charges 4 and 5 (6 July), one plug-in as two pieces merged: 7 kWh over the first hour, 14 kWh over the
second, ten minutes apart.
"""
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

from test_a_days_heading_sums_up_its_trips import _client

_CHARGE = ("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,"
           " duration_min, charge_type, location_type) VALUES (?,1,?,?,40,60,7.0,60,'AC','HOME')")
_SAMPLE = ("INSERT INTO positions (vehicle_id, recorded_at, charging, charge_voltage_v, charge_current_a, soc,"
           " battery_min_temp, outside_temp) VALUES (1,?,1,400,-17.5,?,?,?)")


def _at(day, hour, minute=0):
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'UTC')")
    for cid, day, temps in ((1, 3, True), (2, 4, False), (3, 5, None)):
        c.execute(_CHARGE, (cid, _at(day, 8).isoformat(), _at(day, 9).isoformat()))
        if temps is None:
            continue
        for k in range(13):
            c.execute(_SAMPLE, ((_at(day, 8) + timedelta(minutes=5 * k)).isoformat(), 40 + 20 * k / 12,
                                23 + 15 * k / 12 if temps else None, 22 if temps else None))
    for cid, start, kwh in ((4, _at(6, 8), 7.0), (5, _at(6, 9, 10), 14.0)):
        c.execute(_CHARGE.replace("7.0,60", "?,60"), (cid, start.isoformat(), (start + timedelta(hours=1)).isoformat(), kwh))
        for k in range(13):
            c.execute(_SAMPLE, ((start + timedelta(minutes=5 * k)).isoformat(), 40 + k, 25, 20))
    c.execute("UPDATE charges SET merged_into_id = 4 WHERE id = 5")
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return _client()


def _facts(client, cid):
    html = client.get(f"/api/charge/{cid}/power-chart").text
    m = re.search(r'<div class="charge-facts[^>]*>(.*?)</div>', html, re.DOTALL)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", p)).strip() for p in re.findall(r"<span>(.*?)</span>", m.group(1))] if m else None


def test_the_line_says_the_batterys_warming_the_air_and_the_average_power(client):
    assert _facts(client, 1) == ["🔋 Battery temp 23 → 38 °C", "🌡 Outside 22 °C", "⚡ 7.0 kW on average"]


def test_without_temperatures_only_the_average_power(client):
    assert _facts(client, 2) == ["⚡ 7.0 kW on average"]


def test_without_readings_there_is_no_line(client):
    html = client.get("/api/charge/3/power-chart").text
    assert "charge-facts" not in html and "No power data" in html


def test_the_line_comes_above_the_charts_legend(client):
    html = client.get("/api/charge/1/power-chart").text
    assert html.index('class="charge-facts') < html.index('id="pcl-1"') < html.index('id="pc-1"')


def test_an_outside_air_that_changed_is_its_lowest_to_its_highest(client):
    """26 °C half-way, 22 °C again at the end: the range, not the first and the last reading."""
    import sqlite3
    with sqlite3.connect(db_reader.DB_PATH) as c:
        c.execute("UPDATE positions SET outside_temp = 26.4 WHERE recorded_at = ?", (_at(3, 8, 30).isoformat(),))
    assert _facts(client, 1)[1] == "🌡 Outside 22 – 26.4 °C"


def test_a_battery_that_kept_its_temperature_says_it_once(client):
    assert _facts(client, 5)[0] == "🔋 Battery temp 25 °C"


def test_the_readers_unit(client):
    db_reader.set_setting("unit_system", "imperial_us")
    assert _facts(client, 1)[:2] == ["🔋 Battery temp 73 → 100 °F", "🌡 Outside 71.6 °F"]


def test_every_language_can_say_on_average():
    locales = pathlib.Path(db_reader.__file__).resolve().parent / "locales"
    files = sorted(locales.glob("*.json"))
    assert len(files) == 8
    for f in files:
        assert json.loads(f.read_text())["translations"].get("charge_avg_power"), f.name


def test_a_merged_charges_average_is_over_the_whole_session(client):
    """21 kWh over the 130 minutes from the first piece's start to the second's end, as the row shows the
    merged charge, not the first piece's 7 kWh over its hour; a piece's own chart (a page opened before the
    merge) keeps the piece's own average; after a split each piece has its own again."""
    assert _facts(client, 4)[-1] == "⚡ 9.7 kW on average"
    assert _facts(client, 5)[-1] == "⚡ 14.0 kW on average"
    db_reader.unmerge_charges(4)
    assert _facts(client, 4)[-1] == "⚡ 7.0 kW on average" and _facts(client, 5)[-1] == "⚡ 14.0 kW on average"
