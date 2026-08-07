"""One drive, two pages, two answers — the trip detail printed a dash for a figure the trips list
was already printing (@michapr, beta #27, 07/08/26).

He asked why a generator trip records no average consumption. The first answer measured his OTHER
idea — dividing by the electric-only kilometres — and rejected it with his own numbers, correctly:
two of three such trips landed below the minimum of his real electric ones. But that was not what he
was asking for. His arithmetic divides by the WHOLE distance and shows the two energies as a PAIR:

    1.2 kWh over 68 km  →  1.8 kWh/100km        (the electric half)
    4.6 L   over 68 km  →  6.8 L/100km          (the other half)

*"I think the 1.2 kWh are right. Because all other energy is coming from generator — counted by fuel
usage."* And he is right that it is not a missing measurement: it is the electric part of what the
drive cost, and the litres beside it are the rest.

🔴 The part worth not repeating: Mate had been computing exactly that number all along and printing
it on the trips list (`partials/trip_row.html`), while this page dashed for the same trip.
`get_trip_detail` has called `_reev_trip_elec` since Phase D and the template simply never used the
result — so the disagreement was between two of our own templates, not between us and him.

⚠️ It stays OUT of the AVG CONSUMPTION tile. getEC counts roughly what LEFT THE BATTERY, and the
generator drives the wheels without passing through the pack, so 1.8 here and 13.1 on a pure
electric trip are different quantities. Printed under one label they would invite the comparison —
so it is marked instead (blue, ⚡, beside the litres), which is his own suggestion: *"in brackets or
in other color to show that it is a calculated value only"*.
"""
import pathlib
import re

import pytest

import db as D
import db_reader

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "templates" / "trip_detail.html"
LIST_ROW = ROOT / "web" / "templates" / "partials" / "trip_row.html"

LABELS = {"battery_net": "Battery change", "energy_used": "Energy used"}


def _trip(tmp_path, monkeypatch, *, km=68.0, ec=1.2, fuel_from=80.0, fuel_to=71.0):
    """His 28 July drive, near enough: 68 km, 1.2 kWh metered out of the pack, and a tank that
    dropped while the generator ran. `efficiency_kwh_100km` stays NULL — that is what the app
    writes for a generator trip, and it is why AVG CONSUMPTION shows a dash."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " efficiency_kwh_100km, start_soc, end_soc, ec_kwh, fuel_start_pct, fuel_end_pct)"
        " VALUES (1,1,?,?,?,NULL,80,74,?,?,?)",
        ("2026-07-28T10:00:00+00:00", "2026-07-28T12:00:00+00:00", km, ec, fuel_from, fuel_to))
    pdb._conn.commit()
    db_reader.set_setting("is_reev", "1")
    return db_reader.get_trip_detail(1)


def _render_tile(trip, *, is_reev=True, research=True):
    """Render the real tile out of the real template — a hand-written stand-in cannot fail on a
    guard that lives in Jinja.

    ⚠️ The end marker also closes an earlier tile; slicing on the first hit from position 0 yields
    an empty block that renders to "" and makes every assertion below compare "" with "" — see the
    same note in test_reev_trip_energy_tile_is_getec."""
    jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")
    src = TEMPLATE.read_text()
    start = src.index("{% if is_reev %}")
    end = src.index("{% endif %}\n        </div>", start) + len("{% endif %}")
    env = jinja2.Environment()
    env.filters["dec"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f}"
    # The app's `nice`: at most 2 decimals, trailing zeros stripped (web/main._nice). Restated here
    # rather than imported — `web/main` and `poller/main` share a name, and reaching for one of them
    # from a test is how the wrong module gets loaded (see mate-two-main-py-collision).
    env.filters["nice"] = lambda v: "—" if v is None else f"{float(v):.2f}".rstrip("0").rstrip(".")
    out = env.from_string(src[start:end]).render(
        trip=trip, is_reev=is_reev, research=research, t=lambda k: LABELS[k])
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out)).strip()


# ── the fix ───────────────────────────────────────────────────────────────────

def test_the_detail_prints_the_electric_rate_on_a_generator_trip(tmp_path, monkeypatch):
    """The whole point. 1.2 kWh over 68 km is 1.76 → 1.8 kWh/100km, and this page used to say
    nothing at all about it."""
    trip = _trip(tmp_path, monkeypatch)
    assert trip["reev_elec_kwh_100km"] == 1.8          # computed all along…
    assert "1.8 kWh/100km" in _render_tile(trip)       # …and now printed


def test_the_two_pages_agree(tmp_path, monkeypatch):
    """🔴 The defect itself, stated as a test: the list printed this figure and the detail did not.
    Read on the sources, so it holds however either page is later restyled."""
    assert "reev_elec_kwh_100km" in LIST_ROW.read_text(), "the trips list stopped showing it"
    assert "reev_elec_kwh_100km" in TEMPLATE.read_text(), "the trip detail is silent again"


def test_the_litres_are_still_printed_beside_it(tmp_path, monkeypatch):
    """The pair is the answer — the electric half alone would be the same half-story in reverse."""
    visible = _render_tile(_trip(tmp_path, monkeypatch))
    assert "1.8 kWh/100km" in visible and "L/100km" in visible, visible


# ── and what it must NOT become ───────────────────────────────────────────────

def test_it_never_lands_in_the_avg_consumption_tile(tmp_path, monkeypatch):
    """⚠️ getEC is what left the BATTERY; the generator's feed to the wheels never passes through
    the pack. Under the AVG CONSUMPTION label this 1.8 would sit where a pure electric trip prints
    13.1 — two quantities, one word. That tile keeps reading `efficiency_kwh_100km`, which the
    poller withholds on purpose for a generator trip, so it still shows a dash."""
    src = TEMPLATE.read_text()
    tile = src[src.index("{{ t('avg_efficiency') }}"):src.index("{% if is_reev %}")]
    assert "reev_elec" not in tile, "the electric rate leaked into the AVG CONSUMPTION tile"
    assert _trip(tmp_path, monkeypatch)["efficiency_kwh_100km"] is None


def test_the_kwh_total_is_not_reprinted(tmp_path, monkeypatch):
    """Beta #27 opened with the same number three times in one column. The ⚡ line carries the RATE
    only; the 1.2 kWh is the stat value directly above it."""
    visible = _render_tile(_trip(tmp_path, monkeypatch))
    assert visible.count("1.2") == 1, visible


def test_the_official_build_is_left_alone(tmp_path, monkeypatch):
    """`research`-gated to match the trips list exactly. Gating one page and not the other would
    just move the disagreement to the other build — which is the bug this closes."""
    trip = _trip(tmp_path, monkeypatch)
    assert "kWh/100km" not in _render_tile(trip, research=False)
    assert "{% if is_reev and research and trip.engine_ran %}" in LIST_ROW.read_text() \
        or "research" in LIST_ROW.read_text(), "the list lost its research gate"


def test_a_pure_electric_drive_on_a_reev_prints_no_rate(tmp_path, monkeypatch):
    """The tank did not move, so the generator never ran and the ordinary consumption applies.
    `_reev_trip_elec` returns None and the line must stay away."""
    trip = _trip(tmp_path, monkeypatch, fuel_from=80.0, fuel_to=80.0)
    assert trip["engine_ran"] is False
    assert trip["reev_elec_kwh_100km"] is None
    assert "kWh/100km" not in _render_tile(trip)


def test_a_car_with_no_tank_never_reaches_the_line(tmp_path, monkeypatch):
    """The whole addition lives inside the tile's `{% if is_reev %}` branch. Checked by slicing the
    branch out, not by counting braces."""
    src = TEMPLATE.read_text()
    i = src.index("{% if is_reev %}")
    branch = src[i:src.index("{% elif", i)]
    assert "reev_elec_kwh_100km" in branch, "the rate is printed outside the range-extender branch"
    assert "kWh/100km" not in _render_tile(_trip(tmp_path, monkeypatch), is_reev=False)


# ── the page as a whole ───────────────────────────────────────────────────────

def test_the_whole_template_still_compiles():
    """An edit that leaves an unbalanced block takes the entire page down and no assertion above
    would notice — every one of them renders a slice."""
    jinja2 = pytest.importorskip("jinja2", reason="needs jinja2")
    jinja2.Environment().parse(TEMPLATE.read_text())
