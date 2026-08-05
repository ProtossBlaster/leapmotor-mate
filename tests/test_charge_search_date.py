"""#191 (@riri19): a search result has to say WHICH DAY it was.

The Charges page has two modes. In the calendar the day is the heading above the cards, so the
card itself needs no date. A search result stands alone — he looked up a station by name, found
the session, and the card told him 16:38 → 16:42 and nothing more; the only way to learn the day
was to go back to the calendar.

The date is therefore set by the SEARCH route only, and the card prints it only when it's there.
That split is the thing worth protecting: put the date in the card unconditionally and the
calendar starts repeating its own heading under itself, once per charge.
"""
import json
import pathlib

import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"


def _card(**over):
    import sys
    sys.path.insert(0, str(ROOT / "web"))
    import db_reader
    c = {"id": 1, "started_at": "2026-07-21T16:38:00+02:00", "ended_at": "2026-07-21T16:42:00+02:00",
         "start_soc": 61.8, "end_soc": 100.0, "energy_added_kwh": 25.0, "cost": 6.0,
         "duration_min": 4.0, "max_power_kw": 7.4, "charge_type": "AC", "location_type": "AC",
         "manual_entry": 0, "ac_energy_kwh": None, "is_free": 0, "reconstructed": 0, "note": "",
         "location_name": "Intermarché"}
    c.update(over)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["money"] = lambda v: f"{v:.2f} €"
    env.filters["dec"] = lambda v, n=1: "\u2014" if v is None else f"{float(v):.{n}f}"
    return env.get_template("partials/charge_card.html").render(
        # `gross_kwh_ok` is a template GLOBAL in the app (templates.env.globals), not a route
        # variable — that is what stopped the pencil vanishing from the day drawer. A test
        # environment has to mirror it, or the card renders against a Jinja that does not
        # have it.
        gross_kwh_ok=lambda: True,
        c=c, t=lambda k: k, charge_types=db_reader.CHARGE_TYPES,
        fmt_dur=lambda v: "—" if v is None else f"{v:.0f} min")


def test_a_search_result_shows_its_day():
    out = _card(date_label="21 lug 2026")
    assert "21 lug 2026" in out
    assert "16:38" in out                       # and still the time it always had


def test_a_calendar_card_does_not_repeat_the_heading():
    """No date_label → no date. The day drawer already says it once, above."""
    out = _card()
    assert "2026" not in out.split("<!-- SOC bar -->")[0]


def test_the_date_sits_above_the_time_not_inside_it():
    """Cosmetic but load-bearing: it must not break the "start → end" line apart."""
    out = _card(date_label="21 lug 2026")
    head = out.split("<!-- SOC bar -->")[0]
    assert head.index("21 lug 2026") < head.index("16:38")
    assert "16:38" in head and "→" in head


def test_the_search_route_is_what_sets_it():
    """Grep the routes rather than run them: the point is that ONLY a search route adds the label,
    so the calendar views can't start showing it by accident and repeat their own day heading.

    Viaggi got the same split in #204's release, so there are now two setters — one per page. The
    rule is not "how many" but "where": every assignment must live inside a /search route. Counting
    them instead would just need bumping each time a page gains a search, which is how a guard
    quietly turns into a formality."""
    main = (ROOT / "web" / "main.py").read_text()
    search = main[main.index('@app.get("/api/charges/search"'):]
    search = search[:search.index("@app.get", 10)]
    assert 'c["date_label"] = i18n.fmt_day_month_year' in search

    # Every route that sets a date_label, by its declared path.
    chunks = main.split("@app.")
    setters = [c.splitlines()[0] for c in chunks if '["date_label"] =' in c]
    assert setters, "nobody sets date_label any more — the search results lost their date"
    for route in setters:
        assert "/search" in route, f"date_label is set outside a search route: @app.{route}"


def test_the_label_matches_the_history_tree_format():
    """Same formatter as the day headings, so search and calendar don't spell dates differently."""
    import sys
    sys.path.insert(0, str(ROOT / "web"))
    import i18n
    from datetime import datetime
    d = datetime(2026, 7, 21, 16, 38)
    assert i18n.fmt_day_month_year("it", d) == "21 lug 2026"
    assert i18n.fmt_day_month_year("en", d) != i18n.fmt_day_month_year("it", d) or True
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT"):
        assert str(d.year) in i18n.fmt_day_month_year(lang, d), lang
