"""«✎ Manual» is back where people used it — add-on #2 (@termy91it, 19/09/2026).

v3.16.0 (PR #284) split the price typed by hand from the charge's type: the badge answers "what kind
of charge was this", and the total paid lives in a column of its own, `cost_manual`. The data model
was right. The page was a regression for everyone who had used Manual — measured on a database
built with v3.15.18, priced the old way, then opened by v3.17.2:

  · the type menu had lost its "✎ Manual" row, the place where the receipt's total was typed, and
    the new place is a bare pencil beside the badge;
  · every charge already priced by hand came back as "❓ To confirm" and into the banner count —
    "3 charges to confirm", two of them priced long ago (18.40 € and 6.00 €, both still stored);
  · typing a price on a new charge no longer settled it: it stayed "To confirm".

The rule now: a charge with a price typed by hand and no real type is "✎ Manual" — on its badge
and in every count that used to ask for it again (`db_reader.is_manual_charge`, one definition).
The menu offers the Manual row with its price box again. The price stays independent of the
type, which was the point of v3.16.0: picking a type afterwards keeps the price, and typing a
price on a typed charge keeps its type.
"""
import json
import pathlib
import re

import db as D
import db_reader
import jinja2
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"
LOCALES = ROOT / "web" / "locales"
EUR = db_reader.CURRENCIES["EUR"]


# ── the database: one definition, every count ──────────────────────────────────

def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _row(pdb, cid, day, *, ctype, cost=None, cost_manual=0, charge_type="DC", kw=60.0):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type, cost, cost_manual, charge_type, max_power_kw)"
        " VALUES (?,1,?,?,40,60,10.0,?,?,?,?,?)",
        (cid, f"2026-09-{day:02d}T10:00:00+00:00", f"2026-09-{day:02d}T10:40:00+00:00",
         ctype, cost, cost_manual, charge_type, kw))
    pdb._conn.commit()


def _his_charges(pdb):
    """What an owner who used Manual has after the upgrade: two charges priced with the old type
    (the migration marked them cost_manual=1), one ordinary Home charge, one the car recorded that
    nobody has touched yet, and — the newest — one priced with the pencil and given no type."""
    _row(pdb, 1, 10, ctype="MANUAL", cost=18.40, cost_manual=1)
    _row(pdb, 2, 12, ctype="MANUAL", cost=6.00, cost_manual=1, charge_type="AC", kw=7.0)
    _row(pdb, 3, 14, ctype="HOME", cost=4.10, charge_type="AC", kw=7.2)
    _row(pdb, 4, 16, ctype=None)
    _row(pdb, 5, 18, ctype=None, cost=15.00, cost_manual=1)


def test_a_charge_priced_by_hand_is_not_asked_for_again(tmp_path, monkeypatch):
    """The banner said 3 on his data; one charge was actually waiting."""
    _his_charges(_setup(tmp_path, monkeypatch))
    assert db_reader.unconfirmed_charges_count() == 1


def test_the_banner_link_goes_to_the_one_that_is_waiting(tmp_path, monkeypatch):
    """The newest charge is priced by hand; the link must skip it for the one that needs a type."""
    _his_charges(_setup(tmp_path, monkeypatch))
    assert db_reader.newest_unconfirmed_charge_id() == 4


def test_the_home_public_card_counts_manual_charges_as_manual(tmp_path, monkeypatch):
    """Not public — a Manual charge made at home is not public (the PR #284 review) — and not
    waiting either: a fourth count, and the four still add up to the total."""
    _his_charges(_setup(tmp_path, monkeypatch))
    s = db_reader.get_ac_dc_stats()
    assert (s["home_count"], s["public_count"], s["manual_count"], s["unconfirmed_count"]) == (1, 0, 3, 1)
    assert s["home_count"] + s["public_count"] + s["manual_count"] + s["unconfirmed_count"] == s["total"]
    assert s["manual_kwh"] == 30.0


def test_the_monthly_report_gives_manual_charges_their_own_line(tmp_path, monkeypatch):
    """The report counted the old Manual as public and the pencil's price as unconfirmed — two
    answers for one kind of charge. One line now, the same as the card's."""
    _his_charges(_setup(tmp_path, monkeypatch))
    cur = db_reader.get_monthly_report("2026-09")["cur"]
    assert cur["manual"] == {"count": 3, "kwh": 30.0, "cost": 39.4}
    assert cur["public"]["count"] == 0
    assert cur["unconfirmed"] == 1


def test_the_search_finds_manual_charges_again(tmp_path, monkeypatch):
    """Until v3.15.18 the search's type filter offered Manual, like any other type."""
    _his_charges(_setup(tmp_path, monkeypatch))
    assert sorted(c["id"] for c in db_reader.search_charges(charge_type="MANUAL")) == [1, 2, 5]


def test_one_definition_of_a_manual_charge():
    rows = [({"location_type": "MANUAL", "cost_manual": 1}, True),
            ({"location_type": None, "cost_manual": 1}, True),
            ({"location_type": None, "cost_manual": 0}, False),
            ({"location_type": "FAST", "cost_manual": 1}, False),   # typed: its type wins
            ({"location_type": None}, False)]                       # before the migration
    assert [db_reader.is_manual_charge(r) for r, _ in rows] == [want for _, want in rows]


# ── the page ───────────────────────────────────────────────────────────────────

class _Quiet(jinja2.Undefined):
    """The card pulls in a dozen filters and globals that have nothing to do with the badge; they
    resolve to nothing here. The assertions are on the badge's own words and the menu's forms."""
    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        return self

    def __getitem__(self, key):
        return self

    def __str__(self):
        return ""


def _env():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True,
                             undefined=_Quiet)
    env.filters["money"] = lambda v: f"{v:.2f}"
    env.filters["nice"] = lambda v: f"{v}"
    env.filters["dec"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f}"
    env.filters["dist"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f} km"
    env.globals["gross_kwh_ok"] = lambda: True
    env.globals["charge_energy"] = db_reader.charge_energy_view
    env.globals["billed_kwh"] = db_reader._billed_kwh
    env.globals["solar_kwh_ok"] = lambda: True
    env.globals["solar_mode_on"] = lambda: False
    env.globals["dist_unit"] = lambda: "km"
    env.globals["dist_val"] = lambda v, n=1: None if v is None else round(float(v), n)
    return env


def _charge(**over):
    c = {"id": 1, "started_at": "2026-09-18T10:00:00+02:00", "ended_at": "2026-09-18T10:45:00+02:00",
         "start_soc": 40.0, "end_soc": 70.0, "energy_added_kwh": 20.0, "cost": None, "cost_manual": 0,
         "duration_min": 45.0, "max_power_kw": 60.0, "charge_type": "DC", "location_type": None,
         "manual_entry": 0, "ac_energy_kwh": None, "is_free": 0, "reconstructed": 0, "note": "",
         "odometer_km": None}
    c.update(over)
    return c


def _render(name, **ctx):
    return _env().get_template(name).render(
        currency=EUR, t=lambda k, **kw: k, charge_types=db_reader.CHARGE_TYPES,
        fmt_dur=lambda v: "—" if v is None else f"{v:.0f} min", **ctx)


def _badge(html, cid=1):
    """The words on the badge: the button that opens this charge's type menu."""
    m = re.search(r"<button onclick=\"document\.getElementById\('ct-menu-%d'\)[^>]*>(.*?)</button>"
                  % cid, html, re.S)
    assert m, "no type badge"
    return " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())


def _menu(html, cid=1):
    start = html.index(f'id="ct-menu-{cid}"')
    return html[start:]


@pytest.mark.parametrize("legacy", ["MANUAL", None], ids=["old Manual type", "pencil, no type"])
def test_a_charge_priced_by_hand_without_a_type_reads_manual(legacy):
    badge = _badge(_render("partials/charge_type_badge.html",
                           charge=_charge(location_type=legacy, cost=18.40, cost_manual=1)))
    assert "✎" in badge and "charge_manual" in badge
    assert "charge_unconfirmed" not in badge


def test_a_charge_with_neither_type_nor_price_still_asks():
    badge = _badge(_render("partials/charge_type_badge.html", charge=_charge()))
    assert "charge_unconfirmed" in badge and "charge_manual" not in badge


def test_a_typed_charge_keeps_its_type_on_the_badge_when_priced_by_hand():
    badge = _badge(_render("partials/charge_type_badge.html",
                           charge=_charge(location_type="FAST", cost=31.60, cost_manual=1)))
    assert "DC" in badge and "charge_manual" not in badge


@pytest.mark.parametrize("lt,cost,cm", [(None, None, 0), ("MANUAL", 6.0, 1), (None, 15.0, 1),
                                        ("FAST", 31.6, 1), ("HOME", None, 0)])
def test_the_type_menu_has_the_manual_row_with_its_price_box(lt, cost, cm):
    """Where the price was typed until v3.15.18, on every charge whatever its type — and it answers
    with the whole selector, so the badge beside it changes as soon as the price goes in."""
    menu = _menu(_render("partials/charge_type_badge.html",
                         charge=_charge(location_type=lt, cost=cost, cost_manual=cm)))
    form = re.search(r'<form[^>]*hx-post="api/charges/1/cost"[^>]*>.*?</form>', menu, re.S)
    assert form, "the type menu has no Manual row"
    row = form.group(0)
    assert 'hx-target="#charge-type-1"' in row
    box = re.search(r'<input[^>]*name="cost"[^>]*>', row)
    assert box, "the Manual row has no price box"
    # An empty OK is the pencil's deliberate clear, which reprices the charge; from a menu row it
    # can only be a slip, and the browser must not send it (measured: no request goes out).
    assert re.search(r"\srequired[\s>]", box.group(0)), "an empty OK in the menu would reprice the charge"
    assert "✎" in row and "charge_manual" in row


def test_the_manual_row_shows_the_price_already_typed():
    row = _menu(_render("partials/charge_type_badge.html",
                        charge=_charge(location_type="MANUAL", cost=6.0, cost_manual=1)))
    inp = re.search(r'<input[^>]*name="cost"[^>]*>', row)
    assert inp, "the type menu has no price box"
    assert 'value="6.00"' in inp.group(0)


def test_the_charge_card_draws_the_same_selector():
    """The card carried a second copy of the badge and its menu; a fix to one copy is a fix the
    other forgets. It includes the one partial now."""
    card = _render("partials/charge_card.html", c=_charge(location_type=None, cost=15.0, cost_manual=1))
    assert "charge_manual" in _badge(card)
    assert 'hx-post="api/charges/1/cost"' in _menu(card)


def test_the_pencil_refreshes_the_badge_too():
    """Typing a price in the pencil on an untyped charge turns it Manual: the badge must follow."""
    html = _render("partials/charge_cost_manual.html", charge=_charge())
    form = re.search(r'<form[^>]*hx-post="api/charges/1/cost"[^>]*>', html)
    assert form, "the pencil has no form"
    assert 'hx-target="#charge-type-1"' in form.group(0)


def test_the_search_offers_manual_among_the_types():
    html = _render("charges.html", stats={}, prices={}, status={}, total=5, ac_dc={},
                   unconfirmed=0, station="", station_info=None, cal_year=2026, cal_month=9,
                   cal_open_day=0, cal_years=[2026], highlight=0, vehicle={},
                   charges_have_odometer=True, request=type("R", (), {"headers": {}})(),
                   page="charges", version="test", demo=False)
    select = re.search(r'<select name="type".*?</select>', html, re.S)
    assert select, "no type filter"
    assert re.search(r'<option value="MANUAL">\s*✎ charge_manual\s*</option>', select.group(0))


def test_the_home_public_card_draws_the_manual_charges():
    ac_dc = {"ac": {"count": 2, "kwh": 20.0}, "dc": {"count": 3, "kwh": 30.0}, "total": 5,
             "home_count": 1, "home_kwh": 10.0, "public_count": 0, "public_kwh": 0.0,
             "manual_count": 3, "manual_kwh": 30.0, "unconfirmed_count": 1, "unconfirmed_kwh": 10.0}
    html = _render("charges.html", stats={}, prices={}, status={}, total=5, ac_dc=ac_dc,
                   unconfirmed=1, station="", station_info=None, cal_year=2026, cal_month=9,
                   cal_open_day=0, cal_years=[2026], highlight=0, vehicle={},
                   charges_have_odometer=True, request=type("R", (), {"headers": {}})(),
                   page="charges", version="test", demo=False)
    start = html.index("home_public_distribution")
    card = html[start:html.index("<script>", start)]
    assert "charge_manual" in card
    series = re.search(r"getElementById\('homepublic-chart'\).*?series: \[([^\]]*)\]", html, re.S)
    assert series, "no Home vs Public ring"
    assert [int(x) for x in series.group(1).split(",")] == [1, 0, 3, 1]


def test_the_monthly_report_prints_the_manual_line(tmp_path, monkeypatch):
    """The Manual charges left "public" in the report's data; a page that did not print their line
    would have dropped them from the split altogether. The real route, off his charges."""
    client = _client(tmp_path, monkeypatch)
    _his_charges(D.Database(str(tmp_path / "t.db")))
    html = client.get("/report?month=2026-09").text
    start = html.index("<!-- Home vs Public split")
    split = html[start:html.index('<div class="card grid grid-cols-3', start)]
    line = re.search(r"✎ Manual</span></span>\s*<span[^>]*>(.*?)</span>\s*</div>", split, re.S)
    assert line, "the report has no Manual line"
    assert " ".join(re.sub(r"<[^>]+>", " ", line.group(1)).split()) == "3 · 30 kWh · 39.40 €"
    bar = re.search(r'<div class="flex h-2\.5[^>]*>(.*?)</div>\s*</div>', split, re.S)
    assert bar and "#94a3b8" in bar.group(1), "the split bar leaves the Manual charges out"


# ── the route, as the page calls it ────────────────────────────────────────────

def _client(tmp_path, monkeypatch):
    """The real app on an empty database of this test's own, in English."""
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    from starlette.testclient import TestClient
    import main

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _setup(tmp_path, monkeypatch)
    db_reader.set_setting("setup_complete", "1")
    return TestClient(main.app)


def test_typing_a_price_on_a_new_charge_settles_it(tmp_path, monkeypatch):

    client = _client(tmp_path, monkeypatch)
    _row(D.Database(str(tmp_path / "t.db")), 7, 18, ctype=None)
    assert db_reader.unconfirmed_charges_count() == 1

    r = client.post("/api/charges/7/cost", data={"cost": "15.00"})

    assert r.status_code == 200
    assert 'id="charge-type-7"' in r.text            # the whole selector comes back, badge included
    badge = _badge(r.text, 7)
    assert "✎" in badge and "Manual" in badge
    assert db_reader.unconfirmed_charges_count() == 0


@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_every_language_names_manual_again(path):
    text = json.loads(path.read_text(encoding="utf-8"))["translations"].get("charge_manual")
    assert text and text.strip(), f"{path.stem} has no word for Manual"
