"""Viaggi 'calendar' Month view + search — same lean pattern as the Ricariche calendar
(tests/test_charges_calendar_search.py): a month grid of day totals, a day-drawer loaded
lazily on click, and a text+advanced-filter search that falls back to the calendar when
cleared. Also covers get_merge_candidates, the dedicated 🔗 view that replaced the old
accordion's inline connectors (see main.py's trips_merge_candidates docstring for why).
Runs on a tmp_path DB (poller schema), CI-safe."""
import asyncio

import pytest

import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _seed(pdb, tid, started, *, ended=None, km=10.0, eff=18.0, regen=0.5, duration=15.0,
          start_soc=60, end_soc=50, note=None, drive_mode=None, merged_into=None):
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " efficiency_kwh_100km, regen_kwh, duration_min, note, drive_mode, merged_into_id)"
        " VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, started, ended or started, km, start_soc, end_soc, eff, regen, duration,
         note, drive_mode, merged_into))
    pdb._conn.commit()


# ── get_trips_calendar_month: per-day totals ──────────────────────────────────

def test_calendar_month_day_totals(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", km=10, eff=15)
    _seed(pdb, 2, "2026-07-04T14:00:00+00:00", km=20, eff=20)
    _seed(pdb, 3, "2026-07-10T10:00:00+00:00", km=5)
    _seed(pdb, 4, "2026-08-01T10:00:00+00:00", km=99)   # different month, excluded

    cal = db_reader.get_trips_calendar_month(2026, 7)
    assert cal["days"][4]["count"] == 2
    assert cal["days"][4]["km"] == 30.0
    # weighted avg: (10*15 + 20*20) / 30 = 18.33, rounded to 1 decimal
    assert cal["days"][4]["avg_eff"] == 18.3
    assert cal["total"]["count"] == 3
    assert cal["total"]["km"] == 35.0


def test_calendar_month_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cal = db_reader.get_trips_calendar_month(2026, 7)
    assert cal["days"] == {}
    assert cal["total"]["count"] == 0


# ── get_trips_calendar_day ─────────────────────────────────────────────────────

def test_calendar_day_trips_most_recent_first(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", note="first")
    _seed(pdb, 2, "2026-07-04T18:00:00+00:00", note="second")
    _seed(pdb, 3, "2026-07-10T10:00:00+00:00", note="other day")
    trips = db_reader.get_trips_calendar_day(2026, 7, 4)
    assert [t["note"] for t in trips] == ["second", "first"]


def test_calendar_day_no_trips_is_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert db_reader.get_trips_calendar_day(2026, 7, 4) == []


# ── search_trips: text + advanced filters ─────────────────────────────────────

def test_search_text_matches_note(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", note="traffico in autostrada")
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", note="strada libera")
    res = db_reader.search_trips(text="autostrada")
    assert [t["id"] for t in res] == [1]


def test_search_by_drive_mode(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", drive_mode="comfort")
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", drive_mode="sport")
    assert [t["id"] for t in db_reader.search_trips(drive_mode="sport")] == [2]


def test_search_km_range(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", km=5)
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", km=50)
    assert [t["id"] for t in db_reader.search_trips(km_min=20)] == [2]
    assert [t["id"] for t in db_reader.search_trips(km_max=20)] == [1]


def test_search_duration_range(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", duration=5)
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", duration=60)
    assert [t["id"] for t in db_reader.search_trips(duration_min=30)] == [2]
    assert [t["id"] for t in db_reader.search_trips(duration_max=30)] == [1]


def test_search_date_range_is_inclusive_local_dates(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00")
    _seed(pdb, 2, "2026-07-10T10:00:00+00:00")
    _seed(pdb, 3, "2026-07-20T10:00:00+00:00")
    res = db_reader.search_trips(date_from="2026-07-04", date_to="2026-07-10")
    assert {t["id"] for t in res} == {1, 2}


def test_search_no_filters_returns_full_history_most_recent_first(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00")
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00")
    assert [t["id"] for t in db_reader.search_trips()] == [2, 1]


# ── get_trip_years / get_trip_local_date ──────────────────────────────────────

def test_trip_years_distinct_descending(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2024-03-01T10:00:00+00:00")
    _seed(pdb, 2, "2026-07-04T10:00:00+00:00")
    _seed(pdb, 3, "2026-07-05T10:00:00+00:00")
    assert db_reader.get_trip_years() == [2026, 2024]


def test_trip_local_date_resolves_and_missing_is_none(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00")
    d = db_reader.get_trip_local_date(1)
    assert (d.year, d.month, d.day) == (2026, 7, 4)
    assert db_reader.get_trip_local_date(999) is None


# ── get_merge_candidates: the 🔗 dedicated view ────────────────────────────────

def test_merge_candidates_hydrates_pairs_with_full_trip_data(tmp_path, monkeypatch):
    """A short stop between two trips (no SoC rise → no charge in the gap) is a real
    candidate — both trips come back fully hydrated (note, km, etc.), not just bare ids,
    since the dedicated view (unlike the old inline connectors) has no accordion row to
    pull the rest of the data from."""
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", ended="2026-07-04T10:20:00+00:00",
          end_soc=55, note="leg one")
    _seed(pdb, 2, "2026-07-04T10:23:00+00:00", start_soc=55, note="leg two")   # 3 min gap, no charge
    candidates = db_reader.get_merge_candidates(gap_min=5)
    assert len(candidates) == 1
    pair = candidates[0]
    assert pair["a"]["id"] == 1 and pair["a"]["note"] == "leg one"
    assert pair["b"]["id"] == 2 and pair["b"]["note"] == "leg two"
    assert pair["gap_min"] == 3


def test_merge_candidates_excludes_pair_with_charge_in_gap(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", ended="2026-07-04T10:20:00+00:00", end_soc=40)
    _seed(pdb, 2, "2026-07-04T10:23:00+00:00", start_soc=80)   # SoC rose → charged in the gap
    assert db_reader.get_merge_candidates(gap_min=5) == []


def test_merge_candidates_none_when_no_pairs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert db_reader.get_merge_candidates() == []


# ── main.py wiring: the empty-search fallback ─────────────────────────────────

class _Req:
    """Minimal Starlette Request stand-in — these endpoints only read query params
    (already bound by FastAPI) and pass `request` straight to TemplateResponse."""


def test_trips_search_falls_back_to_calendar_when_all_filters_empty(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00")

    resp = asyncio.run(main.trips_search(_Req(), year=2026, month=7))
    body = resp.body.decode()
    assert 'id="trips-calendar-month"' in body
    assert 'class="trip-row"' not in body


def test_trips_search_with_text_returns_flat_results(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", note="mountain pass")

    resp = asyncio.run(main.trips_search(_Req(), q="mountain"))
    body = resp.body.decode()
    assert 'data-trip-id="1"' in body
    assert 'id="trips-calendar-month"' not in body


def test_trips_search_empty_numeric_fields_dont_422(tmp_path, monkeypatch):
    """#175: an unfilled advanced-filter number input still submits its name with an EMPTY
    value (km_min=""), which a bare `float | None` FastAPI param 422s trying to parse —
    htmx then silently does nothing. Only `drive_mode` set, every numeric field an empty
    string exactly as the real browser form submits them."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", drive_mode="sport")
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", drive_mode="comfort")

    resp = asyncio.run(main.trips_search(
        _Req(), drive_mode="sport", km_min="", km_max="", eff_min="", eff_max="",
        duration_min="", duration_max="", date_from="", date_to=""))
    assert resp.status_code == 200
    body = resp.body.decode()
    assert 'data-trip-id="1"' in body
    assert 'data-trip-id="2"' not in body
