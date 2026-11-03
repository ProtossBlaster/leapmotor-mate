"""Statistics' months and years add up to its Energy Used: each sums its trips' energy by Energy Used's
own rule, and so do the months of the Trips calendar and of the monthly report that the diagnostic
bundle carries beside them. A month used to multiply its average consumption, rounded to a tenth,
back by its kilometres, and to leave out the energy of a trip without kilometres, which Energy Used counts.
"""
import re

import db_reader
from test_a_days_heading_sums_up_its_trips import _client, _install, _sql


def _a_july(tmp_path, monkeypatch):
    """500 km on 81.6 kWh, an average of 16.32 that a tenth makes 16.3 (81.5 kWh), and 0.4 kWh without moving."""
    _install(tmp_path, monkeypatch, [("08:00", "16:00", 90, 20), ("18:00", "18:20", 20, 19)])
    _sql("UPDATE trips SET ec_kwh = CASE id WHEN 1 THEN 81.6 ELSE 0.4 END, ec_stable = 1,"
         " distance_km = CASE id WHEN 1 THEN 500 ELSE 0 END")


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _kwh(text):
    return float(re.search(r"([\d.]+) kWh", text).group(1))


def test_a_month_and_its_year_add_up_to_energy_used(tmp_path, monkeypatch):
    _a_july(tmp_path, monkeypatch)
    _sql("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
    page = _client().get("/statistics").text
    month = _text(re.search(r'id="sm-2026-07".*?</summary>', page, re.S).group(0))
    year = _text(re.search(r'id="sy-2026".*?</summary>', page, re.S).group(0))
    used = _text(page.split('stat-label">Energy Used<', 1)[1].split("stat-label", 1)[0])
    assert _kwh(month) == _kwh(year) == _kwh(used) == 82.0, (month, year, used)


def test_the_bundles_months_agree_with_it(tmp_path, monkeypatch):
    _a_july(tmp_path, monkeypatch)
    assert db_reader.get_trips_calendar_month(2026, 7)["total"]["kwh_used"] == 82.0, "the Trips calendar"
    assert db_reader._collect_monthly_buckets()["2026-07"]["total_kwh_used"] == 82.0, "the monthly report"


def test_no_order_of_adding_the_trips_moves_a_total(tmp_path, monkeypatch):
    """1.001, 1.008 and 1.106 kWh: added oldest first the floats make 3.115, newest first 3.1149999999999998,
    either side of a hundredth. Every place gives the exact sum's, 3.12."""
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 90, 88), ("10:00", "10:30", 88, 86), ("12:00", "12:30", 86, 84)])
    _sql("UPDATE trips SET ec_kwh = CASE id WHEN 1 THEN 1.001 WHEN 2 THEN 1.008 ELSE 1.106 END, ec_stable = 1")
    _sql("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")
    page = _client().get("/statistics").text
    month = _text(re.search(r'id="sm-2026-07".*?</summary>', page, re.S).group(0))
    used = _text(page.split('stat-label">Energy Used<', 1)[1].split("stat-label", 1)[0])
    assert _kwh(month) == _kwh(used) == 3.12, (month, used)
    assert db_reader.trips_totals(db_reader.get_trips())["kwh_used"] == 3.12, "the totals behind Trips"
    assert db_reader._collect_monthly_buckets()["2026-07"]["total_kwh_used"] == 3.12, "the monthly report"
