"""What 100 km have COST — every euro spent, over every kilometre driven (db_reader).

The range-extender version was written as working code by @michapr on his own fork (30/07/26) and
never offered as a pull request. His priced the CONSUMPTION: efficiency × the rate paid per kWh. So
did the first version here, and Silvio cut it short on 05/08 — *«se deve essere un costo deve essere
totale non parziale»*. He is right about what that leaves out. Measured on his own history for #207,
only **71.8%** of a bill reaches the trips at all; the rest leaves the battery standing still, on
climate, preconditioning and the on-board charger's losses, and it was money he paid.

So this divides MONEY by KILOMETRES and nothing else. There is no €/kWh in it, which is why the
meter-versus-battery question — the subject of half the tests this file used to hold — simply
stopped existing: `cost` is what the charge was billed, and billed costs are added, not divided
into anything.

What is left to get wrong is the direction of the error. Anything Mate was not told — an untagged
charge, a refuel nobody entered — takes kilometres out of nothing and puts euros nowhere, so the
figure can only ever come out LOW. Every test below that touches missing data checks that it is
counted and named, never quietly completed.
"""
import json
import pathlib

import db as D
import db_reader
import pytest
import units

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATS = (ROOT / "web" / "templates" / "statistics.html").read_text()
MAIN = (ROOT / "web" / "main.py").read_text()
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))
KEYS = ("stats_cost100_label", "stats_cost100_note", "stats_cost100_elec", "stats_cost100_fuel",
        "stats_cost100_noelec", "stats_cost100_nofuel", "stats_cost100_partial")


@pytest.fixture
def reev(tmp_path, monkeypatch):
    """200 km driven, so the divisor is not 100 and cannot hide a factor-of-two slip: a fixture that
    drives exactly 100 km makes "total" and "per 100 km" the same number, and the first version of
    this file had one — a mutation that priced the lifetime total passed every test in it."""
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','B10')")
    database.set_setting("is_reev", "1")
    database.set_setting("battery_capacity_kwh", "50.0")
    database._conn.commit()
    return database


@pytest.fixture
def bev(tmp_path, monkeypatch):
    path = str(tmp_path / "b.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    database._conn.commit()
    return database


def _trip(db, km=200.0, soc=(80.0, 40.0), fuel=(50.0, 30.0), day=1):
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct) VALUES (1,?,?,?,?,?,?,?)",
        (f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T09:00:00+00:00",
         km, soc[0], soc[1], fuel[0] if fuel else None, fuel[1] if fuel else None))
    db._conn.commit()


def _charge(db, kwh, cost, *, meter=None, home=False, day=1):
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, cost, ac_energy_kwh, location_type) VALUES (1,?,?,20,60,?,?,?,?)",
        (f"2026-06-{day:02d}T08:00:00+00:00", f"2026-06-{day:02d}T09:00:00+00:00",
         kwh, cost, meter, "HOME" if home else None))
    db._conn.commit()


def _refuel(db, litres, total_cost, day=1):
    db._conn.execute(
        "CREATE TABLE IF NOT EXISTS fuel_purchases (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " vehicle_id INTEGER, ts TEXT NOT NULL, liters REAL NOT NULL, price_per_l REAL NOT NULL,"
        " total_cost REAL, fuel_before_pct REAL, note TEXT, created_at TEXT)")
    db._conn.execute(
        "INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost)"
        " VALUES (1,?,?,?,?)",
        (f"2026-06-{day:02d}T10:00:00+00:00", litres,
         (total_cost / litres) if total_cost is not None and litres else 0.0, total_cost))
    db._conn.commit()


def _card():
    """Exactly what the /statistics route does on a range-extender — the litres burned are passed
    only so a car with a tank looks at the fuel table and a car without one never does."""
    t = db_reader.reev_total_consumption()
    return db_reader.cost_per_100km(t["total_fuel_l"] if t else 0.0)


# ── the division itself ───────────────────────────────────────────────────────────────────────

def test_it_is_everything_spent_over_everything_driven(reev):
    """200 km, 5.00 € of electricity, and 40 litres bought at 1.80 of which **10 were burned**
    → 2.50 + 9.00 = 11.50 € per 100 km.

    ⚠️ This test used to assert **36.00** on the fuel side — the whole 72 € purchase over 200 km —
    and it was asserting a defect. @michapr saw it as 24.18 €/100km against the Trips page's 3.87
    for the same petrol (beta #25, 06/08/26): 12 € a litre, because ~50 of the 60 litres he had
    bought were still in the tank. Money is still divided by kilometres and nothing here is a rate;
    what changed is WHICH money — the petrol burned, not the petrol bought, priced at the same
    blended €/L the Trips page uses. `reev_actual_spend` still sums the purchases, and should."""
    _trip(reev)
    _charge(reev, kwh=20.0, cost=5.00)
    _refuel(reev, litres=40.0, total_cost=72.00)
    out = _card()
    assert out["elec_100km"] == 2.50
    assert out["fuel_100km"] == 9.00        # 10 L burned × 1.80 €/L over 200 km
    assert out["total_100km"] == 11.50      # was 38.50, when the whole tank was billed
    assert out["km"] == 200.0


def test_energy_that_never_became_a_trip_is_counted_too(bev):
    """Silvio's whole point, 05/08. The second charge went on climate, preconditioning and the
    charger's own heat: it moved the car nowhere and it was still paid for. Pricing the CONSUMPTION
    — what the first version of this card did — would report 2.50 and drop the other 5.00 on the
    floor. Measured on his own history for #207: 28% of a bill lands nowhere near a trip."""
    _trip(bev, fuel=None)
    _charge(bev, kwh=20.0, cost=5.00, day=1)
    _charge(bev, kwh=20.0, cost=5.00, day=2)
    assert db_reader.cost_per_100km()["total_100km"] == 5.00


def test_the_charging_loss_is_paid_for_and_so_it_counts(bev):
    """A home wallbox billed 5.50 for 22.0 kWh at the wall; 20.0 reached the battery. The 2.0 kWh
    the on-board charger turned into heat are on the bill, so they are in this figure — whole, at
    what they cost, with no conversion factor anywhere. `cost` is already what was billed."""
    _trip(bev, fuel=None)
    _charge(bev, kwh=20.0, cost=5.50, meter=22.0, home=True)
    assert db_reader.cost_per_100km()["total_100km"] == 2.75


def test_a_charge_that_cost_nothing_is_still_a_price(reev):
    """#218: `cost = 0.0` is only ever written deliberately — own solar, a FREE type, a band at
    zero. Read as falsy it becomes an UNPRICED charge, and the card starts warning that a figure is
    incomplete when it is exactly right. Free energy lowers a cost per 100 km; it does not make it
    unknown."""
    _trip(reev, fuel=None)
    _charge(reev, kwh=20.0, cost=5.00, day=1)
    _charge(reev, kwh=20.0, cost=0.00, day=2)
    out = _card()
    assert out["total_100km"] == 2.50
    assert out["partial"] is False, "a free charge is priced, not missing"
    assert out["priced_charges"] == 2


def test_a_negative_cost_is_not_a_discount(reev):
    """Nonsense rather than a price — the same guard `_wac_blend` applies. It must not be subtracted
    from what was spent."""
    _trip(reev, fuel=None)
    _charge(reev, kwh=20.0, cost=5.00, day=1)
    _charge(reev, kwh=20.0, cost=-3.00, day=2)
    assert _card()["total_100km"] == 2.50


# ── the plain electric car ────────────────────────────────────────────────────────────────────

def test_a_bev_divides_its_bill_by_its_kilometres(bev):
    _trip(bev, fuel=None)
    _charge(bev, kwh=20.0, cost=5.00)
    out = db_reader.cost_per_100km()
    assert out["total_100km"] == 2.50
    assert out["elec_100km"] == 2.50
    assert out["fuel_100km"] is None and out["fuel_missing"] is False


def test_a_bev_never_reads_the_fuel_table(bev):
    """Not even when rows are sitting in it — a database whose owner once had the range-extender
    variant selected keeps them, and petrol must not appear on a car with no tank."""
    _trip(bev, fuel=None)
    _charge(bev, kwh=20.0, cost=5.00)
    _refuel(bev, litres=40.0, total_cost=72.00)
    out = db_reader.cost_per_100km()
    assert out["fuel_100km"] is None and out["fuel_entries"] == 0
    assert out["total_100km"] == 2.50, "the total is the electricity alone"


def test_nothing_driven_means_no_card(bev):
    _charge(bev, kwh=20.0, cost=5.00)
    assert db_reader.cost_per_100km() is None


def test_the_divisor_is_the_distance_the_page_already_shows(bev):
    """The note says «diviso i 971 km percorsi» a couple of centimetres under a card headed
    DISTANZA TOTALE that says 971 km. If those two ever came from different row sets — one counting
    an unfinished trip, or another vehicle — the page would carry two distances under one word, and
    the reader would be right to trust neither. Same WHERE, and this is what keeps it that way."""
    _trip(bev, km=200.0, fuel=None, day=1)
    _trip(bev, km=140.0, fuel=None, day=2)
    bev._conn.execute(                                   # still running: in neither figure
        "INSERT INTO trips (vehicle_id, started_at, distance_km, start_soc)"
        " VALUES (1,'2026-07-09T08:00:00+00:00', 90.0, 80)")
    bev._conn.execute(                                   # another car: in neither figure
        "INSERT INTO vehicles (id, vin, car_type) VALUES (2,'Z','B10')")
    bev._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (2,'2026-07-03T08:00:00+00:00','2026-07-03T09:00:00+00:00',500,80,40)")
    bev._conn.commit()
    _charge(bev, kwh=20.0, cost=5.00)
    assert db_reader.cost_per_100km()["km"] == db_reader.get_stats_summary()["total_km"] == 340.0


def test_it_says_when_its_own_window_opens(bev):
    """Silvio, 05/08: the card has to say these are Mate's kilometres SINCE MATE STARTED, not the
    car's odometer. Measured on his own B10 the same day — 4803 km on the dashboard, 1877 recorded
    here — and the note read «diviso i 1877 km percorsi», which sounds like the car has done 1877.

    The window opens at the earliest row that feeds the figure, whichever kind it is: a charge that
    predates every trip is money already in the numerator, so the date has to reach back to it."""
    _trip(bev, fuel=None, day=9)
    _charge(bev, kwh=20.0, cost=5.00, day=3)          # June, before the July trip
    _charge(bev, kwh=20.0, cost=5.00, day=20)         # …and a later one, so "first" ≠ "any"
    assert db_reader.cost_per_100km()["since"].startswith("2026-06-03")


def test_the_window_opens_at_the_first_trip_when_that_came_first(bev):
    _trip(bev, fuel=None, day=1)
    bev._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, cost)"
        " VALUES (1,'2026-08-02T08:00:00+00:00','2026-08-02T09:00:00+00:00',20,5.0)")
    bev._conn.commit()
    assert db_reader.cost_per_100km()["since"].startswith("2026-07-01")


def test_an_unpriced_charge_does_not_open_the_window_early(bev):
    """It contributes nothing to the numerator, so claiming the figure covers a period reaching
    back to it would overstate what the card actually knows."""
    _trip(bev, fuel=None, day=9)
    _charge(bev, kwh=20.0, cost=None, day=1)          # earliest row, but no money in it
    _charge(bev, kwh=20.0, cost=5.00, day=5)
    assert db_reader.cost_per_100km()["since"].startswith("2026-06-05")


def test_the_page_dates_itself_from_the_oldest_thing_it_shows(bev):
    """The line at the top speaks for the WHOLE page — trips, charges, energy, cost — so its date
    reaches back to whichever came first. Unlike the cost card's own window, an unpriced charge
    counts here: the page shows it (ENERGIA CARICATA, the charge count) even with no euros on it."""
    _trip(bev, fuel=None, day=9)
    _charge(bev, kwh=20.0, cost=None, day=2)          # no price, but the page still counts it
    assert db_reader.get_stats_summary()["since"].startswith("2026-06-02")


def test_the_page_has_no_date_before_anything_happened(bev):
    assert db_reader.get_stats_summary()["since"] is None


def test_a_trip_still_running_is_not_in_the_divisor(bev):
    """Found by a mutation nothing caught: dropping `ended_at IS NOT NULL` from the kilometres
    query changed no test, because none of them had an open trip in it.

    A trip in progress has kilometres and no settled energy behind it yet, so counting them divides
    a finished bill by an unfinished distance and reports a cost that drops while you drive and
    climbs back when you park."""
    _trip(bev, fuel=None)
    bev._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, distance_km, start_soc)"
        " VALUES (1,'2026-07-09T08:00:00+00:00', 200.0, 80)")
    bev._conn.commit()
    _charge(bev, kwh=20.0, cost=5.00)
    out = db_reader.cost_per_100km()
    assert out["km"] == 200.0, "only the finished trip"
    assert out["total_100km"] == 2.50


def test_nothing_priced_means_no_card(bev):
    _trip(bev, fuel=None)
    _charge(bev, kwh=20.0, cost=None)
    assert db_reader.cost_per_100km() is None


# ── the error only ever goes one way, and it says so ──────────────────────────────────────────

def test_an_unpriced_charge_is_counted_and_named(reev):
    """It is not dropped and it is not guessed at. The figure shown is a FLOOR — those kilometres
    are in the divisor and that charge's euros are not in the numerator — so the card has to say how
    many are missing, which is what `partial` and the two counts are for."""
    _trip(reev, fuel=None)
    _charge(reev, kwh=20.0, cost=5.00, day=1)
    _charge(reev, kwh=20.0, cost=None, day=2)
    out = _card()
    assert out["total_100km"] == 2.50
    assert out["partial"] is True
    assert (out["priced_charges"], out["total_charges"]) == (1, 2)


def test_petrol_burned_and_never_entered_is_named(reev):
    """A range-extender that burned fuel nobody logged: the electricity alone is not the cost of
    those 100 km, and the card says so rather than letting 2.50 stand as the answer."""
    _trip(reev)
    _charge(reev, kwh=20.0, cost=5.00)
    out = _card()
    assert out["total_100km"] == 2.50
    assert out["fuel_missing"] is True
    assert out["fuel_100km"] is None


def test_a_tank_nobody_touched_is_not_missing(reev):
    """A REEV fortnight on the battery alone burns no petrol. Absent consumption is not absent
    data, and nothing must warn about it."""
    _trip(reev, fuel=(50.0, 50.0))
    _charge(reev, kwh=20.0, cost=5.00)
    out = _card()
    assert out["total_100km"] == 2.50
    assert out["fuel_missing"] is False


def test_no_charge_priced_at_all_is_named_as_a_whole_missing_side(reev):
    """Refuels entered, not one charge priced: the total is the petrol alone and the electricity is
    reported missing whole, not as zero."""
    _trip(reev)
    _charge(reev, kwh=20.0, cost=None)
    _refuel(reev, litres=40.0, total_cost=72.00)
    out = _card()
    assert out["elec_100km"] is None
    assert out["elec_missing"] is True
    assert out["total_100km"] == 9.00       # the petrol BURNED, not the tank bought — beta #25


def test_a_database_without_the_gross_kwh_column_still_answers(reev):
    """v3.6.6 shipped a 500 by naming `gross_kwh` in queries the poller had not yet migrated. This
    one must never need that column, nor any energy column at all — it reads costs and kilometres."""
    _trip(reev, fuel=None)
    _charge(reev, kwh=20.0, cost=5.00)
    try:
        reev._conn.execute("ALTER TABLE charges DROP COLUMN gross_kwh")
        reev._conn.commit()
    except Exception:                       # pragma: no cover - older SQLite
        pytest.skip("SQLite here cannot drop a column")
    assert _card()["total_100km"] == 2.50


# ── in the reader's own units ─────────────────────────────────────────────────────────────────

def test_covering_100_miles_costs_more_than_covering_100_km():
    """The conversion that is easy to get backwards. 100 miles is 160.9 km, so the number GROWS —
    `dist_val` converts the other way and reusing it here would quietly report a third less."""
    assert units.cost100_val(4.00, system="metric") == 4.00
    for system in ("imperial_uk", "imperial_us"):
        assert units.cost100_val(4.00, system=system) == pytest.approx(6.44, abs=0.01)
        assert units.dist100_unit(system) == "100 mi"
    assert units.dist100_unit("metric") == "100 km"


def test_nothing_to_convert_stays_nothing():
    assert units.cost100_val(None, system="imperial_us") is None


# ── and where it shows ────────────────────────────────────────────────────────────────────────

def _card_block() -> str:
    """The card's own block, so these assertions cannot be satisfied by the rest of the page."""
    assert "stats_cost100_label" in STATS
    return STATS.split("{% if totals.cost100 %}", 1)[1].split("<div class=\"grid grid-cols-2", 1)[0]


def test_the_card_is_for_every_car():
    assert "{% if totals.cost100 %}" in STATS
    assert "{% if is_reev and research and totals.cost100 %}" not in STATS


def test_the_petrol_half_stays_behind_the_range_extender_gate():
    card = _card_block()
    assert "{% if is_reev and research and c100.elec_100km and c100.fuel_100km %}" in card
    assert "{% if is_reev and research and c100.fuel_missing %}" in card


def test_the_route_feeds_both_cars_from_the_one_function():
    body = MAIN.split('@app.get("/statistics", response_class=HTMLResponse)', 1)[1] \
               .split("\n@app.", 1)[0]
    assert 'db_reader.cost_per_100km(_rt["total_fuel_l"] if _rt else 0.0)' in body
    assert "totals[\"cost100\"] = db_reader.cost_per_100km()" in body
    # Anchored to code, not to a word: the first version of this looked for "cost" and found it in
    # the comment above the two lines it was checking. A string in a source file is also in its
    # prose — same trap as the `data-holds-selection` test on 04/08.
    for forbidden in ("SELECT ", "SUM(", "energy_added_kwh", "total_cost"):
        assert forbidden not in body, \
            f"the route works the money out for itself ({forbidden!r}); that belongs in db_reader"


def test_no_price_per_kwh_appears_on_the_card():
    """None is computed any more, so none can be shown — and the 04/08 defect (Overview 0.271
    against Ricariche 0.250, both correct, both under one word) cannot come back through here."""
    card = _card_block()
    assert "price3" not in card and "/kWh" not in card


def test_every_figure_is_converted_before_it_is_formatted():
    """A cost per 100 km printed straight through `money` is a per-100-km number under a per-100-mi
    label for anyone on imperial units."""
    card = _card_block()
    assert card.count("cost100_val(") == card.count("| money")
    assert "/100 km" not in card, "a hardcoded unit is what this had to stop doing"
    assert card.count("dist100_unit()") >= 2


def test_the_kilometres_in_the_note_carry_their_own_unit():
    """`dist` converts AND labels; `nice` would print 971 beside the word "mi"."""
    assert "c100.km|dist(0)" in _card_block()


def test_the_page_says_once_that_none_of_this_is_the_car_s_own_total():
    """Silvio's call, 05/08: said ONCE at the top, not defended card by card. Not one figure on
    Statistics is the car's lifetime counter — the page never shows that counter at all — and his
    own B10 reads 4803 km on the dashboard against 1877 recorded here.

    Through `localdate`, so the date lands in the reader's language and timezone; the DB is UTC
    everywhere and only display converts."""
    head = STATS.split("{% block content %}", 1)[1].split("<!-- KPI cards -->", 1)[0]
    assert "stats_recorded_since" in head, "the page-wide line is not in the page header"
    assert "totals.since|localdate" in head
    assert "stats_recorded_since" not in _card_block(), "said once, not twice"


def test_what_is_missing_is_said_out_loud_and_in_amber():
    """The fourth arrived with the energy balance (@michapr, beta #25): an in-window session with
    no kWh figure makes that number a floor, and it is named the same way the missing prices are.
    The count is asserted against the LIST, so adding a warning without marking it still fails."""
    card = _card_block()
    flags = ("c100.elec_missing", "c100.fuel_missing", "c100.partial", "c100.kwh_missing")
    for flag in flags:
        assert flag in card
    assert card.count("text-amber-500") == len(flags), \
        "each missing-data warning is marked, not whispered"


def test_the_partial_warning_counts_the_UNPRICED_ones():
    """`priced_charges` is how many DO have a price; the sentence is about the ones that do not, and
    printing the wrong one of the two reads as "1 of 2 missing" when 1 of 2 is fine."""
    assert "c100.total_charges - c100.priced_charges" in _card_block()


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_words_exist_in_every_language(path):
    """Seven files, not the six @michapr's fork touched — Dutch was the one left out."""
    d = json.loads(path.read_text(encoding="utf-8"))["translations"]
    for key in KEYS:
        assert d.get(key), f"{path.stem} is missing {key}"
    assert not any(k.startswith("stats_reev_cost100_") for k in d), \
        f"{path.stem} still carries the REEV-only name the card outgrew"


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_every_sentence_keeps_its_placeholders(path):
    """A stray brace here is a 500 on the page, not a typo."""
    d = json.loads(path.read_text(encoding="utf-8"))["translations"]
    assert "{unit}" in d["stats_cost100_label"]
    d["stats_cost100_label"].format(unit="100 mi")
    n = d["stats_cost100_note"]
    assert "{km}" in n
    n.format(km="1877 km")


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_page_line_names_mate_and_dates_itself(path):
    """The two facts Silvio asked for on 05/08, in every language: WHOSE data this is, and SINCE
    WHEN. The first note read «diviso i 1877 km percorsi» on a car whose odometer says 4803 — it
    sounded like a claim about the car's mileage. The product name is the same word in all seven
    languages, which is what makes the first of the two checkable at all."""
    d = json.loads(path.read_text(encoding="utf-8"))["translations"]
    s = d.get("stats_recorded_since")
    assert s, f"{path.stem} is missing stats_recorded_since"
    assert "{da}" in s
    s.format(da="20 mag 2026")
    assert "Mate" in s, f"{path.stem}: the line does not say whose data this is"
    s = d["stats_cost100_partial"]
    assert "{n}" in s and "{tot}" in s
    s.format(n=1, tot=7)
    # ONE charge must not read "1 ricariche di 7". Mate's own shape for a counted sentence puts the
    # noun AFTER the total — `avg_price_partial` is "{n} di {tot} sessioni", `stats_energy_partial`
    # is "{n} di {tot} viaggi" — and that is what makes it agree for every n in all seven
    # languages. Anything sitting between the two placeholders is a noun waiting to disagree.
    i, j = s.index("{n}"), s.index("{tot}")
    assert i < j, f"{path.stem}: the count must come before the total"
    assert len(s[i + 3:j].strip()) <= 4, \
        f"{path.stem}: a word sits between {{n}} and {{tot}} — «{s.format(n=1, tot=7)}»"


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_warnings_say_which_way_the_number_is_wrong(path):
    """Missing data can only ever push this figure DOWN, and a warning that does not say so leaves
    the reader to assume the error could go either way."""
    d = json.loads(path.read_text(encoding="utf-8"))["translations"]
    for key in ("stats_cost100_partial", "stats_cost100_nofuel"):
        assert len(d[key]) > 40, f"{path.stem}/{key} is too short to say which direction"
