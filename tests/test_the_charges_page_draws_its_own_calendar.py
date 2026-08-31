"""#240 — the Charges page must never be an empty box under a banner.

@Ng-EY: *"the charging page show the 1 charge(s) to confirm on top but not showing at bottom.
Have to put in a date to make it show up"*. Silvio reproduced it: press Refresh and the calendar
disappears for good on the test instance, and on production the months stop going back.

One cause underneath both. Clicking a day is REMEMBERED (sessionStorage), and from then on
base.html adds `open_day` to every calendar request for that month — so the response renders the
day drawer INLINE, which reaches `charge_card.html`, which prints `currency.symbol`. The
calendar's own context never carried a currency, so it was a 500. htmx swaps nothing on a 500,
which is why nothing appeared and nothing said why: the remembered day kept re-arming it on
every reload. On production the remembered day was in an OLDER month, so only paging back broke.

Three defences here, and each was seen red before it was written green:
  · the grid renders with a day open (the 500 itself);
  · the page carries the grid in its own HTML, so a failed request cannot empty it;
  · the banner is a link to the charge, which is what the reader was asking for all along.
→ [[mate-web-partials-render-standalone]] · [[mate-web-ui-gotchas]]
"""
import pathlib

import db as D
import db_reader
import jinja2
import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
EUR = db_reader.CURRENCIES["EUR"]


class _Quiet(jinja2.Undefined):
    """The page drags in the whole chrome; only what is asserted on matters here.

    ⚠️ It cannot hide the defect under test: `currency.symbol` resolving to nothing would still
    leave the card in the output, and the assertions are on the card being there at all."""
    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        return self

    def __getitem__(self, key):
        return self

    def __str__(self):
        return ""


def _env(undefined: type[jinja2.Undefined] = _Quiet):
    """`undefined=jinja2.Undefined` is what the APP renders with, and it is the only setting that
    can see this defect: a plain `{% if missing %}` is falsy, but `{{ missing.symbol }}` raises —
    which is exactly the 500. `_Quiet` swallows the attribute access too, so a test that stubs the
    chrome with it would have stayed green through the whole of #240.
    → [[feedback-a-green-test-can-assert-the-bug]]"""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True,
                             undefined=undefined)
    env.filters["money"] = lambda v: f"{v:.2f}"
    env.filters["nice"] = lambda v: f"{v:.2f}"
    env.filters["dec"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f}"
    env.filters["dist"] = lambda v, n=1: "—" if v is None else f"{float(v):.1f} km"
    env.globals["gross_kwh_ok"] = lambda: True
    env.globals["solar_kwh_ok"] = lambda: True
    env.globals["solar_mode_on"] = lambda: False
    env.globals["dist_unit"] = lambda: "km"
    env.globals["dist_val"] = lambda v, n=1: None if v is None else round(float(v), n)
    env.globals["absent_temps"] = lambda: []
    return env


def _charge(**over):
    c = {"id": 7, "started_at": "2026-08-08T17:33:00+08:00", "ended_at": "2026-08-08T17:44:00+08:00",
         "start_soc": 48.5, "end_soc": 64.7, "energy_added_kwh": 11.32, "cost": None,
         "duration_min": 11.0, "max_power_kw": 69.1, "charge_type": "DC", "location_type": None,
         "manual_entry": 0, "ac_energy_kwh": None, "is_free": 0, "reconstructed": 0, "note": "",
         "odometer_km": None,
         # the two the calendar's own reader adds on top of the DB row: `date_label` is left to the
         # search results (the day is already the heading here) and `km_since_prev` comes from
         # _km_since_previous_map. Present-and-empty, because that is what the app hands over —
         # under StrictUndefined a missing key would raise where the app renders happily.
         "date_label": "", "km_since_prev": None,
         "latitude": None, "longitude": None, "location_name": "", "location_url": "",
         "gross_kwh": None, "wallbox_energy_start_kwh": None, "wb_stuck_kwh": None}
    c.update(over)
    return c


def _month_ctx(**over):
    """What _charges_calendar_ctx hands the month template."""
    weeks = [[None] * 5 + [{"day": 1, "count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False},
                           {"day": 2, "count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False}],
             [{"day": d, "count": 1 if d == 8 else 0, "kwh": 11.32 if d == 8 else 0.0,
               "cost": 0.0, "has_cost": False} for d in range(3, 10)]]
    import datetime
    ctx = {"t": lambda k, **kw: k, "year": 2026, "month": 8, "weeks": weeks,
           "total": {"count": 1, "kwh": 11.32, "battery_kwh": 11.32, "cost": 0.0, "has_cost": False},
           "month_label": "August 2026", "weekday_abbrs": list("MTWTFSS"),
           "prev_year": 2026, "prev_month": 7, "next_year": 2026, "next_month": 9,
           "today": datetime.date(2026, 8, 9), "station": "",
           "charge_types": db_reader.CHARGE_TYPES,
           "fmt_dur": lambda v: "—" if v is None else f"{v:.0f} min",
           "currency": EUR}
    ctx.update(over)
    return ctx


# ── 1. the 500 itself: the grid with a day open ───────────────────────────────

def test_the_grid_renders_the_day_drawer_inline():
    """🔴 RED before the fix: `UndefinedError: 'currency' is undefined` — the 500 that emptied the
    page. `_month_ctx` mirrors `_charges_calendar_ctx`, so if that route ever stops passing
    something the inline drawer needs, it surfaces here instead of on someone's Charges page."""
    ctx = _month_ctx(open_day=8, open_day_charges=[_charge()], open_day_label="08 Aug 2026")
    out = _env(jinja2.Undefined).get_template("partials/charges_calendar_month.html").render(ctx)
    assert 'name="cost"' in out, "the day's charge card was not drawn"
    assert "11.32" in out


def test_and_the_money_actually_reaches_the_card():
    """The render succeeding is not the point — the symbol has to come out. Asserting only that
    it did not raise would pass on a currency that resolved to nothing."""
    ctx = _month_ctx(open_day=8, open_day_charges=[_charge()], open_day_label="08 Aug 2026",
                     currency=db_reader.CURRENCIES["PLN"])
    out = _env(jinja2.Undefined).get_template("partials/charges_calendar_month.html").render(ctx)
    import re
    box = re.search(r'<input[^>]*name="cost"[^>]*>', out)
    assert box and 'placeholder="zł"' in box.group(0), box and box.group(0)


def test_a_calendar_context_without_a_currency_is_the_500_we_had():
    """The mirror: this is what the route used to hand over, and it must still blow up. A fix that
    made the template shrug instead would hide the next one."""
    ctx = _month_ctx(open_day=8, open_day_charges=[_charge()], open_day_label="08 Aug 2026")
    ctx.pop("currency")
    with pytest.raises(jinja2.exceptions.UndefinedError):
        _env(jinja2.Undefined).get_template("partials/charges_calendar_month.html").render(ctx)


# ── 2. the page carries the grid itself ───────────────────────────────────────

def _page(**over):
    ctx = {"stats": {}, "prices": {}, "status": {}, "total": 4, "ac_dc": {}, "unconfirmed": 0,
           "unconfirmed_id": 0, "station": "", "station_info": None, "cal_year": 2026,
           "cal_month": 8, "cal_open_day": 0, "cal_years": [2026], "highlight": 0, "vehicle": {},
           "charges_have_odometer": True, "currency": EUR, "t": lambda k, **kw: k,
           "charge_types": db_reader.CHARGE_TYPES, "page": "charges", "version": "test",
           "demo": False, "request": type("R", (), {"headers": {"x-ingress-path": ""}})(),
           "fmt_dur": lambda v: "—", "calendar_html": "<div id='charges-calendar-month'>GRID</div>"}
    ctx.update(over)
    return _env().get_template("charges.html").render(ctx)


def test_the_history_is_in_the_page_not_only_in_a_later_request():
    """🔴 RED before: the wrapper shipped a bare `…` and everything below depended on one more
    HTTP round trip that nothing checked and nothing reported when it failed."""
    out = _page()
    assert "GRID" in out, "the calendar is not rendered into the page"
    wrap = out.split('id="charges-calendar-month-wrap"', 1)[1][:600]
    assert "…" not in wrap.split("GRID")[0], "the empty placeholder is still what the page ships"


# ── 3. the banner is a way IN, not an announcement ────────────────────────────

def test_the_banner_links_to_the_charge_to_confirm():
    """🔴 RED before: a <div>. It said a charge needed a type and gave no way to reach it — the
    reader had to guess the day on the calendar. That is #240 in the reporter's own words."""
    out = _page(unconfirmed=1, unconfirmed_id=42)
    banner = out.split("charges_to_confirm")[0]
    assert 'href="charges?highlight=42"' in banner, "the banner is not a link to the charge"


def test_one_charge_is_not_announced_in_the_plural():
    """🔴 RED before: «1 ricariche da confermare». It had always been there, and turning the banner
    into a link put it under the reader's cursor. Same singular/plural pair the app already uses
    for trips — and the seven languages each got their own singular, not an English "(s)"."""
    real_t = _it_translations()
    out = _page(unconfirmed=1, unconfirmed_id=42, t=lambda k, **kw: real_t.get(k, k))
    assert "1</b> ricarica da confermare" in out.replace("\n", " ")
    many = _page(unconfirmed=3, unconfirmed_id=42, t=lambda k, **kw: real_t.get(k, k))
    assert "3</b> ricariche da confermare" in many.replace("\n", " ")


def _it_translations():
    import json
    return json.loads((pathlib.Path(__file__).resolve().parent.parent
                       / "web" / "locales" / "it.json").read_text())["translations"]


def test_every_language_has_its_own_singular():
    """Not an English "(s)" pasted into seven files: each language says one charge its own way."""
    import json
    root = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    forms = {}
    for p in sorted(root.glob("*.json")):
        tr = json.loads(p.read_text())["translations"]
        one, many = tr["charge_to_confirm_one"], tr["charges_to_confirm"]
        assert one and many and one != many, f"{p.name}: singular and plural are the same"
        assert "(s)" not in one and "(s)" not in many, f"{p.name}: still an English bracket-s"
        forms[p.stem] = one
    assert len(set(forms.values())) == len(forms), f"two languages share a singular: {forms}"


def test_a_banner_with_no_id_is_not_a_broken_link():
    """Defensive: if the id ever comes back 0, the banner must not link to `?highlight=0`."""
    out = _page(unconfirmed=1, unconfirmed_id=0)
    assert "highlight=0" not in out


# ── 4. which charge the banner points at ──────────────────────────────────────

@pytest.fixture
def one_car(tmp_path, monkeypatch):
    database = D.Database(str(tmp_path / "p.db"))
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZAAA','C10')")
    database._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))
    return database


def _insert(database, started, ended, location_type, vehicle_id=1):
    database._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, location_type, energy_added_kwh)"
        " VALUES (?,?,?,?,1.0)", (vehicle_id, started, ended, location_type))
    database._conn.commit()


def test_it_is_the_newest_untyped_finished_charge(one_car):
    _insert(one_car, "2026-08-01T10:00:00", "2026-08-01T11:00:00", None)      # id 1, older
    _insert(one_car, "2026-08-08T09:33:00", "2026-08-08T09:44:00", None)      # id 2, the one
    _insert(one_car, "2026-08-09T01:00:00", "2026-08-09T02:00:00", "HOME")    # already typed
    assert db_reader.newest_unconfirmed_charge_id() == 2


def test_a_charge_still_running_is_not_offered(one_car):
    """It cannot be confirmed until it ends — same rule the counter above the banner uses, or the
    two would disagree and the banner would point at a row the list does not show."""
    _insert(one_car, "2026-08-08T09:33:00", "2026-08-08T09:44:00", None)
    _insert(one_car, "2026-08-09T01:00:00", None, None)                       # still charging
    assert db_reader.newest_unconfirmed_charge_id() == 1
    assert db_reader.unconfirmed_charges_count() == 1


def test_nothing_to_confirm_is_zero_not_a_crash(one_car):
    _insert(one_car, "2026-08-01T10:00:00", "2026-08-01T11:00:00", "HOME")
    assert db_reader.newest_unconfirmed_charge_id() == 0


def test_it_never_points_at_the_other_cars_charge(one_car):
    """The banner belongs to the selected car, like every other reading on the page."""
    one_car._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,'LFZBBB','T03')")
    one_car._conn.commit()
    _insert(one_car, "2026-08-01T10:00:00", "2026-08-01T11:00:00", None, vehicle_id=1)
    _insert(one_car, "2026-08-09T10:00:00", "2026-08-09T11:00:00", None, vehicle_id=2)  # newer
    db_reader.set_active_vehicle("LFZAAA")
    assert db_reader.newest_unconfirmed_charge_id() == 1
