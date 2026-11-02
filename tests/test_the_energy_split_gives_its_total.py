"""The energy split's card gives its total beside the distance, duration and average consumption.

The total used to be a grey note in the card's corner; it is now said once, in that row, as "Total
consumption". The Report's tile with the same cloud figure says so too; "Energy Used", the name
Statistics gives Mate's own sum of the trips, only when that sum stands in for a cloud total far below it.
"""
import re

import command_client
from test_a_days_heading_sums_up_its_trips import _client, _install, _sql

SPLIT = {"total_kwh": 295.8, "driving_kwh": 256.9, "ac_kwh": 23.6, "other_kwh": 15.3,
         "driving_pct": 86.8, "ac_pct": 8.0, "other_pct": 5.2}


def test_the_card_gives_its_total_beside_the_distance(tmp_path, monkeypatch):
    import main
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 84, 70)])
    monkeypatch.setattr(main, "_period_cache", {})
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda begin, end: SPLIT)
    card = _client().get("/api/energy-period", params={"period": "alltime"}).text
    row = card.split('border-b border-slate-700/60">', 1)[1].split('<div class="flex w-full h-4', 1)[0]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->|\{#.*?#\}", "", row, flags=re.S))).strip()
    assert text.startswith("Distance ") and text.endswith(" Total consumption 295.8 kWh"), text
    assert card.count("295.8 kWh") == 1


def _report_energy_tile(monkeypatch, total):
    import main
    monkeypatch.setattr(main, "_period_cache", {})
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda begin, end: {**SPLIT, "total_kwh": total})
    html = _client().get("/api/report-driving", params={"month": "2026-07", "refresh": 1}).text
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>|\{#.*?#\}", " ", html.split('<div class="card">')[2], flags=re.S)).strip()


def test_the_reports_tile_names_the_cloud_total_alike(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, [("08:00", "10:00", 84, 50)])
    _sql("UPDATE trips SET distance_km = 100, efficiency_kwh_100km = 15.0")
    assert _report_energy_tile(monkeypatch, 295.8).startswith("Total consumption 295.8 kWh")
    # A cloud total far below the trips' 15 kWh: the tile gives Mate's own sum instead, under its name.
    assert _report_energy_tile(monkeypatch, 1.0).startswith("Energy Used 15 kWh")
