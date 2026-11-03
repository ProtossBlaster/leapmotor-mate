"""Trips says how much energy the trips used, in kWh, in a day's heading, in the month strip and in a
searched period's total.

It is the figure Statistics calls Energy Used, summed over their trips by the same
rule, and it says so on hover or tap. A range-extender's lines stay as they were: their consumption is
the one getEC measured, over the kilometres it covers.
"""
import re

from test_a_days_heading_sums_up_its_trips import _client, _drawer, _install, _sql, _sums

TRIPS = [("08:00", "08:30", 84, 70), ("12:00", "12:30", 70, 62), ("17:00", "17:40", 62, 52)]


def _three_trips(tmp_path, monkeypatch):
    """10 km at 15 and 10 km at 20 kWh/100 km, and 10 km with no consumption: 3.5 kWh over 20 km."""
    _install(tmp_path, monkeypatch, TRIPS)
    _sql("UPDATE trips SET efficiency_kwh_100km = CASE id WHEN 1 THEN 15.0 WHEN 2 THEN 20.0 END, regen_kwh = 0.25")


def _strip(html):
    return re.search(r'justify-center gap-4 text-sm mb-4[^>]*>(.*?)</div>', html, re.DOTALL).group(1)


def test_a_days_heading_says_the_energy_its_trips_used(tmp_path, monkeypatch):
    _three_trips(tmp_path, monkeypatch)
    html = _drawer()
    assert "30 km 1h 00m 3.5 kWh ♻️ 0.75 kWh 17.5" in _sums(html), _sums(html)
    assert 'data-tip="Energy Used">3.5 kWh<' in html


def test_the_month_strip_and_a_searched_period_say_the_energy_their_trips_used(tmp_path, monkeypatch):
    _three_trips(tmp_path, monkeypatch)
    strip = _strip(_client().get("/api/trips/calendar", params={"year": 2026, "month": 7}).text)
    assert 'data-tip="Energy Used">3.5 kWh<' in strip, strip
    found = _strip(_client().get("/api/trips/search", params={"date_from": "2026-07-01", "date_to": "2026-07-31"}).text)
    assert 'data-tip="Energy Used">3.5 kWh<' in found, found


def test_a_range_extender_keeps_its_lines(tmp_path, monkeypatch):
    _three_trips(tmp_path, monkeypatch)
    _sql("INSERT OR REPLACE INTO settings (key, value) VALUES ('is_reev_lfztest0000000001', '1')")
    month = _client().get("/api/trips/calendar", params={"year": 2026, "month": 7, "open_day": 4}).text
    found = _client().get("/api/trips/search", params={"date_from": "2026-07-01", "date_to": "2026-07-31"}).text
    assert all("Energy Used" not in html for html in (month, _drawer(), found))
