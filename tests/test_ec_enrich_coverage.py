"""_enrich_eb_with_trip_totals coverage guard (GitHub #105) — the cloud getEC total covers the
car's WHOLE life, Mate's local trips table only starts at install. Pairing the two over a window
that begins before the first recorded trip produced a nonsense average (riri19's report: 1459.6 kWh
over 821.6 km = 177.7 kWh/100km on the "since beginning" preset, which reaches 2 years back).

The guard: such windows keep the official split but get NO distance/duration/average; `trips_since`
carries the first-trip date for the template's explanatory note. Windows the local DB does cover
stay enriched, including the Trips-page all-time card whose begin is the first trip's local
midnight (hence the 1-day slack)."""
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
import main
import db_reader


EB = {"driving_kwh": 1409.9, "ac_kwh": 21.0, "other_kwh": 28.7, "total_kwh": 1459.6,
      "driving_pct": 96.6, "ac_pct": 1.4, "other_pct": 2.0}

FIRST = int(datetime(2026, 6, 5, 8, 30, tzinfo=timezone.utc).timestamp())  # Mate's first trip


def _patch(monkeypatch, first_ts, totals):
    monkeypatch.setattr(main.db_reader, "get_first_trip_ts", lambda: first_ts)
    monkeypatch.setattr(main.db_reader, "get_trip_totals_between", lambda b, e: totals)


def test_window_before_coverage_drops_distance_and_average(monkeypatch):
    """#105 scenario: 'since beginning' preset (2 years back) on a car older than the install."""
    _patch(monkeypatch, FIRST, {"trip_count": 40, "distance_km": 821.6, "duration_min": 1022})
    begin = int(datetime(2024, 7, 2, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 7, 2, tzinfo=timezone.utc).timestamp())
    out = main._enrich_eb_with_trip_totals(dict(EB), begin, end)
    assert "distance_km" not in out
    assert "duration_min" not in out
    assert "avg_kwh100" not in out
    expected = datetime.fromtimestamp(FIRST, tz=db_reader._local_tz()).strftime("%d/%m/%Y")
    assert out["trips_since"] == expected
    assert out["total_kwh"] == EB["total_kwh"]  # the official cloud split itself is untouched


def test_window_within_coverage_is_enriched(monkeypatch):
    _patch(monkeypatch, FIRST, {"trip_count": 3, "distance_km": 50.0, "duration_min": 90})
    begin = FIRST + 86400
    end = begin + 7 * 86400
    out = main._enrich_eb_with_trip_totals({**EB, "total_kwh": 9.0}, begin, end)
    assert out["distance_km"] == 50.0
    assert out["duration_min"] == 90
    assert out["avg_kwh100"] == 18.0  # 9.0 / 50 * 100
    assert "trips_since" not in out


def test_alltime_midnight_begin_stays_within_slack(monkeypatch):
    """Trips-page all-time card: begin = first trip's local MIDNIGHT (a few hours before the
    trip itself) — must still be enriched, that's what the 1-day slack is for."""
    _patch(monkeypatch, FIRST, {"trip_count": 40, "distance_km": 821.6, "duration_min": 1022})
    begin = FIRST - 10 * 3600
    out = main._enrich_eb_with_trip_totals(dict(EB), begin, begin + 30 * 86400)
    assert out["distance_km"] == 821.6
    assert "trips_since" not in out


def test_no_trips_at_all_keeps_plain_zero_totals(monkeypatch):
    """Empty trips table (first_ts None): pre-guard behaviour — zero totals, no average, no note
    (the template already hides the row when distance is 0)."""
    _patch(monkeypatch, None, {"trip_count": 0, "distance_km": None, "duration_min": None})
    out = main._enrich_eb_with_trip_totals(dict(EB), 0, 1000)
    assert out["distance_km"] == 0
    assert out["duration_min"] == 0
    assert "avg_kwh100" not in out
    assert "trips_since" not in out


def test_no_energy_is_a_noop(monkeypatch):
    _patch(monkeypatch, FIRST, {"trip_count": 0, "distance_km": None, "duration_min": None})
    assert main._enrich_eb_with_trip_totals(None, 0, 1) is None
    eb = {"total_kwh": 0}
    assert main._enrich_eb_with_trip_totals(eb, 0, 1) == {"total_kwh": 0}
