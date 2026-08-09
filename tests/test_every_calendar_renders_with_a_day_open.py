"""#240 was the CHARGES calendar. This asks the same question of all four.

The defect: a calendar request that carries `open_day` renders the day's cards **inside the same
response**, from a context the route builds by hand — and the Charges one never passed `currency`,
so the whole history was a 500. `base.html` adds `open_day` by itself from the day remembered in
`sessionStorage`, so this is not an exotic URL: it is what the page sends after any reload.

Mate has **four** such calendars — charges, trips, refuels, wallbox — each with its own context
built in its own place, each reaching a card partial two includes down. Fixing one and testing one
would have left three copies of the same shape untested. → [[feedback-gate-a-feature-find-every-copy]]

Rendered for real, through the route, against a seeded database: a source-level check would pass on
a context that is missing exactly the key the card needs.
"""
import asyncio
import pathlib

import db as D
import db_reader
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

DAY = (2026, 7, 4)


class _Req:
    """Minimal Starlette Request stand-in — these renderers only hand `request` to
    TemplateResponse (same stand-in as tests/test_charges_calendar_search.py)."""


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """One of everything, all on the same day, so every calendar has a day worth opening."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    # a charge — with a wallbox reading, so the Wallbox calendar has one too
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
              " energy_added_kwh, ac_energy_kwh, cost, location_type, charge_type, max_power_kw,"
              " duration_min, latitude, longitude)"
              " VALUES (1,1,'2026-07-04T08:00:00+00:00','2026-07-04T10:00:00+00:00',30,80,"
              "         20.0,22.5,7.5,'HOME','AC',7.4,120,45.0,9.0)")
    # a second one still untyped, which is what the "to confirm" banner counts
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
              " energy_added_kwh, charge_type) "
              " VALUES (2,1,'2026-07-04T18:00:00+00:00','2026-07-04T18:30:00+00:00',40,60,8.0,'DC')")
    # a trip
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
              " start_soc, end_soc, efficiency_kwh_100km, start_odometer_km, end_odometer_km,"
              " duration_min, ec_kwh)"
              " VALUES (1,1,'2026-07-04T12:00:00+00:00','2026-07-04T12:40:00+00:00',30.0,"
              "         80,72,16.0,10000,10030,40,4.8)")
    # ⚠️ The Wallbox calendar counts a charge only where the poller also recorded a POWER CURVE
    # (positions with charging=1 inside the window). Without these its day drawer never opens at
    # all — and a test that only checked "did it render" was green on that empty page.
    for ts in ("2026-07-04T08:30:00+00:00", "2026-07-04T09:30:00+00:00"):
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, charging, soc,"
                  " charge_voltage_v, charge_current_a) VALUES (1,?,1,50,230.0,32.0)", (ts,))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    # the refuel through db_reader, which creates its own table on first write
    db_reader.add_fuel_purchase("2026-07-04T15:00:00+00:00", 30.0, price_per_l=1.75)
    return pdb


def _body(resp):
    return resp.body.decode()


# ── the four, one per calendar ────────────────────────────────────────────────

def test_charges_calendar_with_the_day_open(seeded, monkeypatch):
    """🔴 This is #240 itself: without `currency` in the context it raised
    `UndefinedError: 'currency' is undefined`, i.e. a 500 where the history should be."""
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    body = _body(main._render_charges_calendar(_Req(), 2026, 7, "", open_day=4))
    assert 'id="charges-calendar-month"' in body
    assert 'data-charge-id="1"' in body, "the day's charge card was not rendered inline"


def _drawer_really_opened(without: str, with_day: str, label: str):
    """A body that merely renders proves nothing: the grid alone renders fine, and that is what
    made #240 look like a data problem. What has to be true is that opening the day ADDED the day's
    content — so compare the two, and look for the day's own heading."""
    assert label in with_day, f"the day's heading {label!r} is not in the response"
    assert label not in without, "the grid alone already carried the heading — bad comparison"
    assert len(with_day) > len(without) + 400, (
        f"opening the day added almost nothing: {len(without)} → {len(with_day)} bytes")


def test_trips_calendar_with_the_day_open(seeded, monkeypatch):
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    label = main.i18n.fmt_day_month_year("en", __import__("datetime").date(*DAY))
    _drawer_really_opened(_body(main._render_trips_calendar(_Req(), 2026, 7)),
                          _body(main._render_trips_calendar(_Req(), 2026, 7, open_day=4)), label)


def test_fuel_calendar_with_the_day_open(seeded, monkeypatch):
    """Called through the renderer, not the route: the route is gated behind REEV + research, and
    the gate is a different question from whether the render is sound. On the container this one
    answered 403 and could not be exercised at all — which is exactly why it gets a test."""
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    label = main.i18n.fmt_day_month_year("en", __import__("datetime").date(*DAY))
    _drawer_really_opened(_body(main._render_fuel_calendar(_Req(), 2026, 7)),
                          _body(main._render_fuel_calendar(_Req(), 2026, 7, open_day=4)), label)


def test_wallbox_calendar_with_the_day_open(seeded, monkeypatch):
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    label = main.i18n.fmt_day_month_year("en", __import__("datetime").date(*DAY))
    _drawer_really_opened(
        _body(asyncio.run(main.wallbox_calendar(_Req(), year=2026, month=7))),
        _body(asyncio.run(main.wallbox_calendar(_Req(), year=2026, month=7, open_day=4))), label)


# ── and the shape itself, so a FIFTH calendar cannot be added without one ──────

def test_no_calendar_still_ships_a_bare_placeholder():
    """All four grids are drawn INTO their page, not fetched after it.

    The wrapper keeps its `hx-trigger="load"` — that is what re-opens the remembered day and what
    the ?highlight= scroll hangs off — but nothing on the page depends on that request returning
    any more. A wrapper that still shipped only `…` is the #240 shape waiting to happen again."""
    tpl = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
    pages = {"charges.html": "charges-calendar-month-wrap",
             "trips.html": "trips-calendar-month-wrap",
             "fuel.html": "fuel-calendar-wrap",
             "wallbox.html": "wallbox-calendar-month-wrap"}
    for name, wrap_id in pages.items():
        body = (tpl / name).read_text()
        assert f'id="{wrap_id}"' in body, f"{name}: the calendar wrapper moved or was renamed"
        block = body.split(f'id="{wrap_id}"', 1)[1].split("</div>", 1)[0]
        assert "calendar_html|safe" in block, f"{name}: the grid is not rendered into the page"
        assert "py-10\">…" not in block, f"{name}: still ships the empty placeholder"


def test_the_grid_really_lands_inside_the_wrapper(tmp_path):
    """The source check above proves the template SAYS it; this proves it comes out.

    ⚠️ Rendered here rather than read off a running instance on purpose: on the test container the
    Rifornimenti page is behind the REEV/research gate and the Wallbox page behind "is a meter
    mapped", so **neither block is drawn there at all** — and a page that never renders the block
    cannot show it working. Reading `id=...wrap` off that instance would have been a green on an
    absent element, which is the same empty-page trap as the wallbox drawer earlier in this file.
    → [[feedback-measure-can-be-an-artefact]]"""
    import jinja2
    tpl = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"

    class _Quiet(jinja2.Undefined):
        def __call__(self, *a, **k): return self
        def __getattr__(self, n): return self
        def __getitem__(self, k): return self
        def __str__(self): return ""

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl)), autoescape=True,
                             undefined=_Quiet)
    for f in ("money", "nice", "dec", "dist", "price3", "pct", "kwh"):
        env.filters[f] = lambda v, *a, **k: str(v)
    for g in ("gross_kwh_ok", "dist_unit", "absent_temps"):
        env.globals[g] = lambda *a, **k: ""
    env.globals["dist_val"] = lambda v, n=1: v

    MARK = "GRID-WAS-HERE"
    pages = {"charges.html": "charges-calendar-month-wrap",
             "trips.html": "trips-calendar-month-wrap",
             "fuel.html": "fuel-calendar-wrap",
             "wallbox.html": "wallbox-calendar-month-wrap"}
    for name, wrap_id in pages.items():
        out = env.get_template(name).render(
            calendar_html=f"<div>{MARK}</div>", t=lambda k, **kw: k,
            request=type("R", (), {"headers": {"x-ingress-path": ""}})(),
            page=name[:-5], version="t", demo=False, total=1, unconfirmed=0, unconfirmed_id=0,
            configured=True, is_reev=True, research=True,          # the gates, held open
            cal_year=2026, cal_month=8, cal_open_day=0, cal_years=[2026], highlight=0)
        assert MARK in out, f"{name}: the grid never reached the page"
        after = out.split(f'id="{wrap_id}"', 1)
        assert len(after) == 2, f"{name}: the wrapper itself is not in the output"
        assert MARK in after[1][:800], f"{name}: the grid is on the page but not inside the wrapper"


def test_every_calendar_partial_is_covered_here():
    """The list of calendars is read off the templates, not typed here. Add a fifth month grid with
    a day drawer and this fails until it has a test of its own — the copy-finding is automatic
    rather than remembered."""
    tpl = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" / "partials"
    grids = sorted(p.stem for p in tpl.glob("*_calendar_month.html"))
    tested = {"charges_calendar_month", "trips_calendar_month",
              "fuel_calendar_month", "wallbox_calendar_month"}
    assert set(grids) == tested, (
        f"a calendar with no open-day test: {sorted(set(grids) - tested)}")
    # and each of them really does render its day drawer inline — that is the risky path
    for g in grids:
        body = (tpl / f"{g}.html").read_text()
        assert "open_day" in body, f"{g}: no inline day drawer?"
        assert "_day_content.html" in body, f"{g}: the drawer is not the shared partial"
