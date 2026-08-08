"""The cost box has to be labelled in the reader's OWN money.

Found by looking at the running app while testing #237, on the very form that was being extended.
Two separate slips, three copies, and neither has ever been caught by a test:

  · the "add a past charge" form printed `{{ currency }}` — the whole metadata DICT — so the
    placeholder read `{'name': 'Euro', 'symbol': '€', 'pos': 'after', 'dec': 2}`, clipped by the
    input to `{'name': 'I`. Since v1.30.0 (#87), which is when that form was written.
  · the two type-and-price boxes hard-coded `€`, which is right for one of the fourteen
    currencies Mate offers and wrong for the other thirteen — a reader in £ or zł is told to type
    euros into a field that will be totalled in their own money.

Rendered, not grepped: a source-level assertion here would pass on a template that renders
`{'name': 'Euro'…}` perfectly happily, and this defect is only visible once something is drawn.
→ [[feedback-a-green-test-can-assert-the-bug]] · [[mate-web-ui-gotchas]]
"""
import pathlib

import db_reader
import jinja2
import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
GBP = db_reader.CURRENCIES["GBP"]
PLN = db_reader.CURRENCIES["PLN"]


class _Quiet(jinja2.Undefined):
    """The Charges page pulls in the whole chrome — a dozen filters and globals that have nothing
    to do with a price box. They resolve to nothing here rather than being stubbed one by one.

    ⚠️ It cannot hide what is being tested: the assertions are on the placeholder's VALUE, so a
    `currency.symbol` that failed to resolve would come back empty and fail just as loudly as the
    dictionary did."""
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
    env.filters["dec"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f}"
    env.filters["dist"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f} km"
    env.globals["gross_kwh_ok"] = lambda: True
    env.globals["dist_unit"] = lambda: "km"
    env.globals["dist_val"] = lambda v, n=1: None if v is None else round(float(v), n)
    return env


def _charge(**over):
    c = {"id": 1, "started_at": "2026-07-02T06:45:00+02:00", "ended_at": "2026-07-02T07:45:00+02:00",
         "start_soc": None, "end_soc": None, "energy_added_kwh": 46.3, "cost": None,
         "duration_min": 60.0, "max_power_kw": None, "charge_type": "AC", "location_type": None,
         "manual_entry": 1, "ac_energy_kwh": None, "is_free": 0, "reconstructed": 0, "note": "",
         "odometer_km": None}
    c.update(over)
    return c


class _Request:
    """base.html reads one header off it, for the Home Assistant ingress path prefix."""
    headers = {"x-ingress-path": ""}


def _render(name, currency, **ctx):
    return _env().get_template(name).render(
        currency=currency, t=lambda k, **kw: k, charge_types=db_reader.CHARGE_TYPES,
        request=_Request(), page="charges", version="test", demo=False,
        fmt_dur=lambda v: "—" if v is None else f"{v:.0f} min", **ctx)


def _cost_placeholder(html):
    """The placeholder of the `cost` input, whichever of the three boxes this template holds."""
    import re
    m = re.search(r'<input[^>]*name="cost"[^>]*>', html)
    assert m, "no cost input in this template"
    p = re.search(r'placeholder="([^"]*)"', m.group(0))
    return p.group(1) if p else ""


# ── the "add a past charge" form ──────────────────────────────────────────────

def test_the_add_form_does_not_print_the_currency_dictionary():
    """🔴 What was on screen: `{'name': 'I` — a Python repr, clipped by the input's width."""
    out = _render("charges.html", GBP, stats={}, prices={}, status={}, total=0, ac_dc={},
                  unconfirmed=0, station="", station_info=None, cal_year=2026, cal_month=8,
                  cal_open_day=0, cal_years=[2026], highlight=0, vehicle={},
                  charges_have_odometer=True)
    ph = _cost_placeholder(out)
    assert "{" not in ph and "name" not in ph, f"the placeholder is a dict: {ph!r}"
    assert ph == "£"


# ── the two type-and-price boxes ──────────────────────────────────────────────

@pytest.mark.parametrize("template,ctx", [
    ("partials/charge_card.html", {"c": _charge()}),
    ("partials/charge_type_badge.html", {"c": _charge(), "charge": _charge()}),
])
def test_the_price_box_is_labelled_in_the_readers_money(template, ctx):
    """Fourteen currencies are on offer and only one of them is the euro. A Polish reader typing
    into a box marked € is being told the wrong thing about a number Mate will then total in
    złoty."""
    assert _cost_placeholder(_render(template, PLN, **ctx)) == "zł"


def test_every_route_that_renders_one_of_these_hands_it_the_currency():
    """🔴 The one-line fix on its own would have broken three pages.

    `charge_card.html` and `charge_type_badge.html` are rendered STANDALONE by the search endpoint
    and by the two type/free toggles, from contexts they build by hand — they inherit nothing from
    `_ctx`. Naming `currency.symbol` in a partial that is handed no `currency` is an UndefinedError
    at render time, which is a 500 on a page that used to work.

    So the check is not "the three I found": it is every TemplateResponse in main.py naming a
    template that reaches one of them, forever. → [[feedback-gate-a-feature-find-every-copy]]
    """
    import re
    main = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()

    # which templates end up rendering a cost box — following `include` one level, which is as
    # deep as this tree goes
    wants = {p.name for p in TEMPLATES.rglob("*.html") if "currency." in p.read_text()}
    for p in TEMPLATES.rglob("*.html"):
        body = p.read_text()
        if any(f'"partials/{w}"' in body or f"'partials/{w}'" in body for w in set(wants)):
            wants.add(p.name)
    assert "charge_card.html" in wants and "charge_type_badge.html" in wants

    missing = []
    for m in re.finditer(r"TemplateResponse\((.{0,1600}?)\n(\s*)\}\)", main, re.S):
        block = m.group(1)
        name = re.search(r'"((?:partials/)?[\w./-]+\.html)"', block)
        if not name or pathlib.Path(name.group(1)).name not in wants:
            continue
        if '"currency"' not in block:
            missing.append(name.group(1))
    assert not missing, f"rendered without a currency: {sorted(set(missing))}"


def test_every_currency_can_label_it():
    """Not one hard-coded symbol left anywhere: each of the fourteen has to come back out."""
    for code, cur in db_reader.CURRENCIES.items():
        got = _cost_placeholder(_render("partials/charge_card.html", cur, c=_charge()))
        assert got == cur["symbol"], f"{code}: expected {cur['symbol']!r}, got {got!r}"
