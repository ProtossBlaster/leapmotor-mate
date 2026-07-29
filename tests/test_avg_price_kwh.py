"""The €/kWh actually paid (#187) — and the trap it walks into.

A charge with no cost still has kWh. Untyped, or of a type with no price configured: Mate
records the energy and leaves `cost` NULL. So the obvious average — the period's spend over the
period's energy — divides real money by energy that was never priced, and reports a price LOWER
than the one you pay. Measured on the test container before this existed: the Report showed
0.199 €/kWh for a month whose real price was 0.250, from ONE untagged charge out of ten.

Hence one rule, one function: `price_coverage` divides by the PRICED charges alone, and hands
back how many of them there were so the page can say so. Because the honest average has a side
effect — it is NOT the two totals on screen divided by each other, and a reader who does that
division by hand must not be left wondering which of the two numbers is lying.
"""
import pytest

import db as D
import db_reader


# ── the rule itself (pure, no DB) ─────────────────────────────────────────────

def test_divides_by_the_priced_energy_not_by_all_of_it():
    """The whole point: 20 € over 80 priced kWh is 0.25, even when 20 more kWh went
    unpriced. Dividing by all 100 would report 0.20 — a fifth off, and plausible."""
    cov = db_reader.price_coverage(20.0, 80.0, 4, 5)
    assert cov["avg_price"] == 0.25


def test_says_when_it_is_only_part_of_the_period():
    cov = db_reader.price_coverage(20.0, 80.0, 4, 5)
    assert cov["partial"] is True
    assert (cov["priced_count"], cov["total_count"]) == (4, 5)


def test_says_nothing_when_every_charge_is_priced():
    """No note to show — the average covers everything, and an unconditional note would
    be noise on the card of every user who tags their charges."""
    assert db_reader.price_coverage(20.0, 80.0, 5, 5)["partial"] is False


def test_no_priced_charge_means_no_price_at_all():
    """Not 0.0 — zero is a real answer (see the free-charge case below) and would read as
    'your electricity is free' to someone who simply hasn't confirmed anything yet."""
    assert db_reader.price_coverage(None, 0.0, 0, 5)["avg_price"] is None
    assert db_reader.price_coverage(0.0, 0.0, 0, 5)["avg_price"] is None
    assert db_reader.price_coverage(20.0, None, 1, 5)["avg_price"] is None


def test_free_charges_really_do_lower_the_average():
    """A FREE charge is PRICED at 0 (#120), not unpriced: 20 € over 80 + 20 free kWh is
    0.20 €/kWh, and that is the true answer — free energy makes your electricity cheaper."""
    cov = db_reader.price_coverage(20.0, 100.0, 5, 5)
    assert cov["avg_price"] == 0.20
    assert cov["partial"] is False


def test_kept_to_three_decimals():
    """0.25 vs 0.199 is the whole conversation; `money`'s two decimals would round both
    0.2496 and 0.2504 onto the same 0.25."""
    assert db_reader.price_coverage(20.0, 80.13, 1, 1)["avg_price"] == 0.25


# ── the two places that show it ───────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")

    def add(kwh, cost, location_type="FAST", ac_kwh=None, day=15, month=3):
        pdb._conn.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh,"
            " ac_energy_kwh, location_type, cost, start_soc, end_soc)"
            f" VALUES (1,'2026-{month:02d}-{day:02d}T10:00:00+00:00',"
            f"'2026-{month:02d}-{day:02d}T11:00:00+00:00',?,?,?,?,20,60)",
            (kwh, ac_kwh, location_type, cost))
        pdb._conn.commit()

    return add


def test_charges_strip_ignores_the_unpriced_energy(env):
    """The regression: 80 kWh paid 20 €, plus 20 kWh never tagged. 0.25, not 0.20."""
    add = env
    add(80.0, 20.0)
    add(20.0, None, location_type=None)

    s = db_reader.get_charge_stats()

    assert s["total_kwh"] == 100.0        # the energy card still counts everything
    assert s["total_cost"] == 20.0
    assert s["avg_price"] == 0.25         # ...but the price does not
    assert s["partial"] is True
    assert (s["priced_count"], s["total_count"]) == (1, 2)


def test_charges_strip_uses_the_wallbox_kwh_for_home(env):
    """Same basis as the cost itself (_billed_kwh): a home charge is billed on the AC the
    wallbox delivered, so the €/kWh must divide by that, not by what reached the battery."""
    add = env
    add(40.0, 10.0, location_type="HOME", ac_kwh=50.0)

    assert db_reader.get_charge_stats()["avg_price"] == 0.20     # 10 / 50, not 10 / 40


def test_charges_strip_is_silent_with_nothing_priced(env):
    add = env
    add(20.0, None, location_type=None)

    s = db_reader.get_charge_stats()
    assert s["avg_price"] is None
    assert s["partial"] is False        # nothing to be partial about — the card shows a dash


def test_monthly_report_ignores_the_unpriced_energy(env):
    """The defect that was already on screen: the Report's «Avg price» divided the month's
    spend by the month's TOTAL energy. One untagged charge was enough to under-report it."""
    add = env
    for _ in range(9):
        add(10.0, 2.5, day=10)          # 90 kWh, 22.50 € → 0.250 €/kWh
    add(30.0, None, location_type=None, day=11)

    r = db_reader.get_monthly_report("2026-03")

    assert r["cur"]["charge_kwh"] == 120.0        # the energy stat still counts all of it
    assert r["avg_price"] == 0.25                 # 22.50 / 90 — NOT 22.50 / 120 = 0.1875
    assert r["price_cov"]["partial"] is True
    assert (r["price_cov"]["priced_count"], r["price_cov"]["total_count"]) == (9, 10)


def test_monthly_report_counts_each_month_on_its_own(env):
    """The coverage note belongs to the month being read, not to the whole history."""
    add = env
    add(80.0, 20.0, month=3)
    add(20.0, None, location_type=None, month=3)
    add(50.0, 20.0, month=4)

    assert db_reader.get_monthly_report("2026-04")["price_cov"]["partial"] is False
    assert db_reader.get_monthly_report("2026-04")["avg_price"] == 0.40
    assert db_reader.get_monthly_report("2026-03")["price_cov"]["partial"] is True


# ── what the pages actually render ────────────────────────────────────────────

def _tpl(name):
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent
            / "web" / "templates" / name).read_text()


def test_the_card_exists_on_the_charges_page():
    src = _tpl("charges.html")
    assert "stats.avg_price" in src
    assert "lg:grid-cols-6" in src, "six cards need six columns, or the last one wraps alone"


def test_both_pages_quote_the_price_to_three_decimals():
    """`money` gives two: it would print the wrong 0.199 and the right 0.250 as 0.20 and
    0.25, hiding both the defect and its fix."""
    for name in ("charges.html", "report.html"):
        src = _tpl(name)
        block = src[src.index("avg_price"):]
        head = block[:block.index("{% endif %}")]
        assert "price3" in head, f"{name} must use the 3-decimal price filter"
        assert "| money" not in head, f"{name} still rounds the €/kWh to 2 decimals"


def test_both_pages_say_what_the_average_covers():
    for name in ("charges.html", "report.html"):
        assert "avg_price_partial" in _tpl(name)


def test_no_locale_defines_the_same_key_twice():
    """A duplicate key parses fine and the LAST one silently wins — which is how the Italian
    note stayed on its old wording through an edit that reported success (caught here, on this
    very key). Nothing else in the suite would notice."""
    import collections
    import json
    import pathlib
    loc = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    for f in sorted(loc.glob("*.json")):
        dups = []

        def hook(pairs, _d=dups):
            c = collections.Counter(k for k, _ in pairs)
            _d.extend(k for k, n in c.items() if n > 1)
            return dict(pairs)

        json.loads(f.read_text(encoding="utf-8"), object_pairs_hook=hook)
        assert not dups, f"{f.name} defines {sorted(set(dups))} more than once"


def test_the_note_stays_short_enough_for_a_phone():
    """Measured in the browser at 375 px: the note has ~136 px, and the longest of these six
    renders at 122 px even with four-digit counts. Characters are a rough stand-in for pixels,
    but they catch the translation that is obviously too long — the Polish 'na podstawie {n} z
    {tot} ładowań' overflowed and wrapped, which is what sent this to the shorter wording."""
    import json
    import pathlib
    loc = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT"):
        d = json.loads((loc / f"{lang}.json").read_text(encoding="utf-8"))["translations"]
        filled = d["avg_price_partial"].format(n=1248, tot=1260)
        assert len(filled) <= 25, f"{lang} note is {len(filled)} chars: {filled!r}"


def test_the_note_exists_in_every_locale():
    """Read the locale FILES, not i18n.get_t(): `get_t` falls back to English for any missing
    key, so the usual `t(key) != key` check passes on a locale that never got translated —
    verified by deleting the Italian line and watching that assertion stay green."""
    import json
    import pathlib
    loc = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT"):
        d = json.loads((loc / f"{lang}.json").read_text(encoding="utf-8"))["translations"]
        assert "avg_price_partial" in d, f"{lang} is missing the key"
        s = d["avg_price_partial"]
        assert "{n}" in s and "{tot}" in s, f"{lang} lost a placeholder: {s!r}"
        assert s.format(n=4, tot=5)      # and it survives being filled in
