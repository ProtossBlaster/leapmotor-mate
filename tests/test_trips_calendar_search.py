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


# ── #204 @riri19: merging moved INTO the day drawer, under the date ───────────
#
# The 🔗 view used to replace the whole calendar with every mergeable pair in history, and a
# trip row prints only a clock — so 22 pairs arrived as bare times with nothing saying which
# day any of them was. Two of them started at 17:52 and 17:53, weeks apart, four rows from
# each other. The drawer's heading already IS the date, so the pairs moved under it.

def _tz(pdb, name):
    pdb._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('timezone',?)", (name,))
    pdb._conn.commit()


def test_merge_candidates_scoped_to_one_day(tmp_path, monkeypatch):
    """`day=` keeps only that day's pairs; `day=None` still returns every one of them."""
    from datetime import date
    pdb = _setup(tmp_path, monkeypatch)
    _tz(pdb, "UTC")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", ended="2026-07-04T10:20:00+00:00", end_soc=55)
    _seed(pdb, 2, "2026-07-04T10:23:00+00:00", ended="2026-07-04T10:40:00+00:00", start_soc=55)
    _seed(pdb, 3, "2026-07-09T08:00:00+00:00", ended="2026-07-09T08:20:00+00:00", end_soc=50)
    _seed(pdb, 4, "2026-07-09T08:22:00+00:00", start_soc=50)

    assert len(db_reader.get_merge_candidates(gap_min=5)) == 2          # unscoped: both days
    only_4th = db_reader.get_merge_candidates(gap_min=5, day=date(2026, 7, 4))
    assert [p["a"]["id"] for p in only_4th] == [1]
    only_9th = db_reader.get_merge_candidates(gap_min=5, day=date(2026, 7, 9))
    assert [p["a"]["id"] for p in only_9th] == [3]
    assert db_reader.get_merge_candidates(gap_min=5, day=date(2026, 7, 5)) == []


def test_merge_candidates_pair_across_midnight_belongs_to_the_earlier_day(tmp_path, monkeypatch):
    """A pair straddling midnight is anchored to the EARLIER trip's day, because the merged
    trip inherits the parent's date and that's the day it will end up on. Never seen in 302
    real trips, but the drawer has to offer it on one day or the other, not neither."""
    from datetime import date
    pdb = _setup(tmp_path, monkeypatch)
    _tz(pdb, "UTC")
    _seed(pdb, 1, "2026-07-04T23:50:00+00:00", ended="2026-07-04T23:58:00+00:00", end_soc=55)
    _seed(pdb, 2, "2026-07-05T00:01:00+00:00", start_soc=55)          # 3 min later, next day

    assert len(db_reader.get_merge_candidates(gap_min=5)) == 1
    assert [p["a"]["id"] for p in
            db_reader.get_merge_candidates(gap_min=5, day=date(2026, 7, 4))] == [1]
    assert db_reader.get_merge_candidates(gap_min=5, day=date(2026, 7, 5)) == []


def test_merge_candidates_day_filter_agrees_with_the_drawer_across_a_tz_shift(tmp_path, monkeypatch):
    """The pairs and the list under them must never disagree about which day a trip is on.
    23:10 UTC is already the 5th in Rome, so the drawer files both trips under the 5th — and
    the pair has to be offered there too, not on the 4th the raw timestamps would suggest."""
    from datetime import date
    pdb = _setup(tmp_path, monkeypatch)
    _tz(pdb, "Europe/Rome")
    _seed(pdb, 1, "2026-07-04T23:10:00+00:00", ended="2026-07-04T23:20:00+00:00", end_soc=55)
    _seed(pdb, 2, "2026-07-04T23:23:00+00:00", start_soc=55)          # both = 5 July in Rome

    assert [t["id"] for t in db_reader.get_trips_calendar_day(2026, 7, 5)] == [2, 1]
    assert [p["a"]["id"] for p in
            db_reader.get_merge_candidates(gap_min=5, day=date(2026, 7, 5))] == [1]
    assert db_reader.get_merge_candidates(gap_min=5, day=date(2026, 7, 4)) == []


def _day(tmp_path, monkeypatch, **kw):
    """Render the real day-drawer partial through the real route."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    return asyncio.run(main.trips_calendar_day(_Req(), year=2026, month=7, day=4, **kw)).body.decode()


def _seed_one_mergeable_day(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _tz(pdb, "UTC")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", ended="2026-07-04T10:20:00+00:00", end_soc=55)
    _seed(pdb, 2, "2026-07-04T10:23:00+00:00", ended="2026-07-04T10:40:00+00:00", start_soc=55)
    _seed(pdb, 3, "2026-07-04T18:00:00+00:00", start_soc=40, end_soc=30)   # alone, not mergeable
    return pdb


def test_day_drawer_merge_mode_shows_only_that_days_pairs_and_keeps_the_slider(tmp_path, monkeypatch):
    _seed_one_mergeable_day(tmp_path, monkeypatch)
    body = _day(tmp_path, monkeypatch, merge=1)

    assert "merge-preview?a=1&b=2" in body        # the connector, on the real pair
    assert 'name="gap"' in body                       # the max-stop slider came along
    assert 'data-trip-id="3"' not in body             # the day's un-mergeable trip is out
    assert "Jul 4, 2026" in body or "4 Jul 2026" in body   # the date is still the heading


def test_day_drawer_without_merge_is_the_plain_list(tmp_path, monkeypatch):
    _seed_one_mergeable_day(tmp_path, monkeypatch)
    body = _day(tmp_path, monkeypatch)

    assert "merge-preview" not in body
    assert 'name="gap"' not in body
    assert 'data-trip-id="3"' in body                 # every trip of the day, including the loner


def test_day_drawer_merge_mode_keeps_the_slider_on_a_day_with_no_pairs(tmp_path, monkeypatch):
    """A wider stop can turn nothing into two, so the empty state must still carry the slider —
    otherwise the reader is stuck at whichever gap the page happened to open on."""
    pdb = _setup(tmp_path, monkeypatch)
    _tz(pdb, "UTC")
    _seed(pdb, 1, "2026-07-04T10:00:00+00:00", ended="2026-07-04T10:20:00+00:00")
    body = _day(tmp_path, monkeypatch, merge=1)

    assert "merge-preview" not in body
    assert 'name="gap"' in body


def test_merge_button_is_on_BOTH_paths_that_render_the_day(tmp_path, monkeypatch):
    """The day content is rendered from TWO places — its own endpoint (clicking a cell) and the
    month view opening straight onto a day (?highlight=, or the day remembered across a reload).
    The 🔗 button needs `day` in scope; the month path binds it through a {% with %}, and if that
    binding is dropped the button silently disappears down exactly one of the two."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    _seed_one_mergeable_day(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")

    via_day_endpoint = asyncio.run(
        main.trips_calendar_day(_Req(), year=2026, month=7, day=4)).body.decode()
    via_month_open_day = asyncio.run(
        main.trips_calendar(_Req(), year=2026, month=7, open_day=4)).body.decode()

    for name, body in (("day endpoint", via_day_endpoint), ("month ?open_day", via_month_open_day)):
        assert "day=4&merge=1" in body, f"the 🔗 button is missing from the {name} render"


# ── #191's split, finally applied to Viaggi too ──────────────────────────────
#
# Ricariche got this in #191 (@riri19): a search result stands alone, so it has to say which day
# it was, while a calendar card must NOT — the date is already the heading above it. Trips kept
# the calendar half and never got the search half: a hit read "17:52 → 18:15" and nothing more.
# The label is set by the SEARCH route only; the row prints it when it's there. Both halves are
# tested, because putting the date in the row unconditionally is the obvious wrong fix.

def _search(tmp_path, monkeypatch, **kw):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    return asyncio.run(main.trips_search(_Req(), **kw)).body.decode()


def test_a_trip_search_result_says_which_day_it_was(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _tz(pdb, "UTC")
    _seed(pdb, 1, "2026-07-04T17:52:00+00:00", ended="2026-07-04T18:15:00+00:00", note="mountain pass")
    body = _search(tmp_path, monkeypatch, q="mountain")

    assert 'data-trip-id="1"' in body
    assert "17:52" in body                                  # the clock is still there
    assert ("Jul 4, 2026" in body) or ("4 Jul 2026" in body), "the search hit carries no date"


def test_the_day_drawer_does_not_repeat_the_date_on_every_row(tmp_path, monkeypatch):
    """The other half of the split, and the one a careless fix breaks: in the drawer the date is
    the heading, so a row must NOT print it — otherwise the heading appears again under itself,
    once per trip."""
    pdb = _setup(tmp_path, monkeypatch)
    _tz(pdb, "UTC")
    _seed(pdb, 1, "2026-07-04T17:52:00+00:00", ended="2026-07-04T18:15:00+00:00")
    _seed(pdb, 2, "2026-07-04T19:00:00+00:00", ended="2026-07-04T19:20:00+00:00")
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    body = asyncio.run(main.trips_calendar_day(_Req(), year=2026, month=7, day=4)).body.decode()

    for label in ("Jul 4, 2026", "4 Jul 2026"):
        assert body.count(label) <= 1, f"'{label}' appears {body.count(label)}× — the heading is repeated per row"
