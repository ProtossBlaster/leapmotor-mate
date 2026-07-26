"""Charges 'calendar' Month view + search — replaces the old year/month/day accordion (still
used nowhere else) with a lean month grid (day totals only, no per-charge markup until a day is
clicked) plus a text+advanced-filter search that falls back to the calendar when cleared.
Runs on a tmp_path DB (poller schema), CI-safe."""
import asyncio

import pytest

import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _seed(pdb, cid, started, *, lat=45.0, lon=9.0, kwh=10.0, cost=None, name=None,
          note=None, location_type=None, ended=None):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, latitude, longitude, cost, location_name, note, location_type)"
        " VALUES (?,1,?,?,30,80,?,?,?,?,?,?,?)",
        (cid, started, ended or started, kwh, lat, lon, cost, name, note, location_type))
    pdb._conn.commit()


# ── get_charges_calendar_month: per-day totals ────────────────────────────────

def test_calendar_month_day_totals(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", kwh=10, cost=5)
    _seed(pdb, 2, "2026-07-04T14:00:00+00:00", kwh=20, cost=8)
    _seed(pdb, 3, "2026-07-10T10:00:00+00:00", kwh=15)                 # no cost
    _seed(pdb, 4, "2026-08-01T10:00:00+00:00", kwh=5)                  # different month

    cal = db_reader.get_charges_calendar_month(2026, 7)
    assert cal["year"] == 2026 and cal["month"] == 7
    assert cal["days"][4] == {"count": 2, "kwh": 30.0, "cost": 13.0, "has_cost": True}
    assert cal["days"][10] == {"count": 1, "kwh": 15.0, "cost": 0.0, "has_cost": False}
    assert 1 not in cal["days"]                                        # August charge excluded
    assert cal["total"] == {"count": 3, "kwh": 45.0, "cost": 13.0, "has_cost": True}


def test_calendar_month_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cal = db_reader.get_charges_calendar_month(2026, 7)
    assert cal["days"] == {}
    assert cal["total"]["count"] == 0


def test_calendar_month_station_filter(tmp_path, monkeypatch):
    """Same 'lat,lon' key convention as get_charges_grouped(station=...) — only that one
    physical station's sessions count toward the month's day totals."""
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", lat=45.070, lon=7.686, kwh=10)
    _seed(pdb, 2, "2026-07-04T14:00:00+00:00", lat=46.000, lon=8.000, kwh=20)
    cal = db_reader.get_charges_calendar_month(2026, 7, station="45.070,7.686")
    assert cal["days"][4]["count"] == 1
    assert cal["days"][4]["kwh"] == 10.0


# ── get_charges_calendar_day: the day-drawer's charge list ───────────────────

def test_calendar_day_charges_most_recent_first(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", name="Enel X")
    _seed(pdb, 2, "2026-07-04T18:00:00+00:00", name="Ionity")
    _seed(pdb, 3, "2026-07-10T10:00:00+00:00", name="Elsewhere")   # different day
    charges = db_reader.get_charges_calendar_day(2026, 7, 4)
    assert [c["location_name"] for c in charges] == ["Ionity", "Enel X"]


def test_calendar_day_no_charges_is_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert db_reader.get_charges_calendar_day(2026, 7, 4) == []


# ── search_charges: text + advanced filters ───────────────────────────────────

def test_search_text_matches_name_or_note(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", name="Ionity Binasco")
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", name="Enel X", note="great spot near Ionity shop")
    _seed(pdb, 3, "2026-07-06T10:00:00+00:00", name="BeCharge")
    res = db_reader.search_charges(text="ionity")
    assert {c["id"] for c in res} == {1, 2}          # name match + note match, case-insensitive


def test_search_by_charge_type(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", location_type="HOME")
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", location_type="HPC")
    res = db_reader.search_charges(charge_type="HPC")
    assert [c["id"] for c in res] == [2]


def test_search_kwh_and_cost_range(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", kwh=10, cost=5)
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", kwh=40, cost=25)
    assert [c["id"] for c in db_reader.search_charges(kwh_min=20)] == [2]
    assert [c["id"] for c in db_reader.search_charges(kwh_max=20)] == [1]
    assert [c["id"] for c in db_reader.search_charges(cost_min=10, cost_max=30)] == [2]


def test_search_date_range_is_inclusive_local_dates(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00")
    _seed(pdb, 2, "2026-07-10T10:00:00+00:00")
    _seed(pdb, 3, "2026-07-20T10:00:00+00:00")
    res = db_reader.search_charges(date_from="2026-07-04", date_to="2026-07-10")
    assert {c["id"] for c in res} == {1, 2}


def test_search_no_filters_returns_full_history_most_recent_first(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00")
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00")
    assert [c["id"] for c in db_reader.search_charges()] == [2, 1]


def test_search_station_filter(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", lat=45.070, lon=7.686)
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", lat=46.000, lon=8.000)
    res = db_reader.search_charges(station="45.070,7.686")
    assert [c["id"] for c in res] == [1]


# ── get_charge_years: populates the calendar's year-jump pills ────────────────

def test_charge_years_distinct_descending(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2024-03-01T10:00:00+00:00")
    _seed(pdb, 2, "2026-07-04T10:00:00+00:00")
    _seed(pdb, 3, "2026-07-05T10:00:00+00:00")   # same year as #2, must not duplicate
    assert db_reader.get_charge_years() == [2026, 2024]


def test_charge_years_empty_when_no_charges(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert db_reader.get_charge_years() == []


def test_charge_years_station_filter(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2024-03-01T10:00:00+00:00", lat=45.070, lon=7.686)
    _seed(pdb, 2, "2026-07-04T10:00:00+00:00", lat=46.000, lon=8.000)
    assert db_reader.get_charge_years(station="45.070,7.686") == [2024]


# ── today_local / get_charge_local_date — the ?highlight= month-resolving helpers ────

def test_today_local_is_a_real_date(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    today = db_reader.today_local()
    assert today.year >= 2026


def test_get_charge_local_date_resolves_and_missing_is_none(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00")
    d = db_reader.get_charge_local_date(1)
    assert (d.year, d.month, d.day) == (2026, 7, 4)
    assert db_reader.get_charge_local_date(999) is None


# ── main.py wiring: the empty-search fallback and the calendar/day endpoints ─────────

class _Req:
    """Minimal Starlette Request stand-in — these endpoints only read query params
    (already bound by FastAPI) and pass `request` straight to TemplateResponse."""


def test_charges_search_falls_back_to_calendar_when_all_filters_empty(tmp_path, monkeypatch):
    """No text, no advanced filter set at all → renders the Month calendar, not an empty
    'no results' list — this is what lets the search bar's own onclear behavior be pure
    server logic instead of a client-side branch."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", kwh=10)

    resp = asyncio.run(main.charges_search(_Req(), year=2026, month=7))
    body = resp.body.decode()
    assert 'id="charges-calendar-month"' in body          # the calendar's own root, not a list
    assert "charge-card" not in body                       # no cards pre-rendered into the grid


def test_charges_search_with_text_returns_flat_results(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", name="Ionity Binasco")

    resp = asyncio.run(main.charges_search(_Req(), q="ionity"))
    body = resp.body.decode()
    assert 'data-charge-id="1"' in body
    assert 'id="charges-calendar-month"' not in body        # a flat list, not the calendar shell


def test_charges_search_empty_numeric_fields_dont_422(tmp_path, monkeypatch):
    """#175: an unfilled advanced-filter number input still submits its name with an EMPTY
    value (cost_min=""), which a bare `float | None` FastAPI param 422s trying to parse —
    htmx then silently does nothing (no 2xx response to swap in). Only `type` set, every
    numeric field an empty string exactly as the real browser form submits them."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", location_type="HPC")
    _seed(pdb, 2, "2026-07-05T10:00:00+00:00", location_type="AC")

    resp = asyncio.run(main.charges_search(
        _Req(), type="HPC", cost_min="", cost_max="", kwh_min="", kwh_max="",
        date_from="", date_to=""))
    assert resp.status_code == 200
    body = resp.body.decode()
    assert 'data-charge-id="1"' in body
    assert 'data-charge-id="2"' not in body
