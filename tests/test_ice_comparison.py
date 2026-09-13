"""What the same kilometres would have cost on a petrol/diesel car the owner names (db_reader).

The feature started as "pick a model, look up its consumption" — a real external database was
checked first: RapidAPI's "Cars Fuel Consumption" turned out to be Canadian NRCan data, 1995-2022,
so it has none of the city cars (Panda, Ypsilon) an Italian owner is most likely to compare
against, and nothing newer than 2022 either. So there is no lookup at all: the owner types a name
and the two numbers already on the comparison car's own fuel label (L/100km, its own €/L), and this
divides those against `cost_per_100km`'s own total — the same total the Statistics page already
shows beside it, never a second, independently-windowed figure.
"""
import json
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATS = (ROOT / "web" / "templates" / "statistics.html").read_text()
COSTS = (ROOT / "web" / "templates" / "costs.html").read_text()
SETTINGS = (ROOT / "web" / "templates" / "settings.html").read_text()
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))
KEYS = ("ice_compare_title", "ice_compare_enable", "ice_compare_name",
        "ice_compare_consumption", "ice_compare_fuel_price", "ice_compare_use_kml",
        "stats_ice_compare_label", "stats_ice_compare_saved", "stats_ice_compare_more")


@pytest.fixture
def bev(tmp_path, monkeypatch):
    path = str(tmp_path / "b.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    database._conn.commit()
    return database


def _trip(db, km=200.0, day=1):
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (1,?,?,?,80,40)",
        (f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T18:00:00+00:00", km))
    db._conn.commit()


def _charge(db, kwh, cost, day=1):
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, cost) VALUES (1,?,?,20,60,?,?)",
        (f"2026-07-{day:02d}T10:00:00+00:00", f"2026-07-{day:02d}T11:00:00+00:00", kwh, cost))
    db._conn.commit()


def _enable(db, l_100km=6.0, price=1.80, name="Fiat Panda 1.2", use_kml=False):
    db.set_setting("ice_compare_enabled", "1")
    db.set_setting("ice_compare_use_kml", "1" if use_kml else "0")
    db.set_setting("ice_compare_l_100km", str(l_100km))
    db.set_setting("ice_compare_fuel_price", str(price))
    db.set_setting("ice_compare_name", name)


# ── the arithmetic ────────────────────────────────────────────────────────────────────────────

def test_it_prices_the_same_kilometres_on_both_cars(bev):
    """200 km. Electric: 5.00 € actually billed → 2.50 €/100km × 200 = 5.00 € total.
    Petrol: 6.0 L/100km × 1.80 €/L = 10.80 €/100km × 200 = 21.60 € total.
    Savings: 21.60 - 5.00 = 16.60 €."""
    _trip(bev)
    _charge(bev, kwh=20.0, cost=5.00)
    _enable(bev)
    out = db_reader.ice_comparison(db_reader.cost_per_100km())
    assert out["actual_total"] == 5.00
    assert out["ice_total"] == 21.60
    assert out["savings"] == 16.60
    assert out["name"] == "Fiat Panda 1.2"
    assert out["km"] == 200.0


def test_a_more_expensive_electric_bill_shows_as_a_negative_saving(bev):
    """An owner on an expensive dynamic tariff, compared against a frugal small car, can come out
    behind — the card must say so rather than clamp the number at zero."""
    _trip(bev, km=100.0)
    _charge(bev, kwh=20.0, cost=30.00)          # 30 €/100km, absurd on purpose
    _enable(bev, l_100km=5.0, price=1.80)       # 9.00 €/100km on petrol
    out = db_reader.ice_comparison(db_reader.cost_per_100km())
    assert out["savings"] == pytest.approx(9.00 - 30.00)
    assert out["savings"] < 0


# ── the unit the owner's own fuel label happens to print ────────────────────────────────────────

def test_km_per_litre_is_converted_before_the_arithmetic_runs():
    """An Italian city car's sticker is as likely to say 16.7 km/l as 6.0 L/100km — same car,
    same fuel economy (100 / 16.7 ≈ 5.988). The two entries must price the same trip alike."""
    l_100km = 100.0 / 16.7
    assert l_100km == pytest.approx(5.988, abs=0.001)


def test_a_kml_entry_prices_the_same_as_its_l_100km_equivalent(bev):
    _trip(bev)
    _charge(bev, kwh=20.0, cost=5.00)
    _enable(bev, l_100km=16.7, use_kml=True)
    kml_out = db_reader.ice_comparison(db_reader.cost_per_100km())
    _enable(bev, l_100km=100.0 / 16.7, use_kml=False)
    l100_out = db_reader.ice_comparison(db_reader.cost_per_100km())
    assert kml_out["ice_total"] == pytest.approx(l100_out["ice_total"], abs=0.01)
    assert kml_out["savings"] == pytest.approx(l100_out["savings"], abs=0.01)


def test_the_display_keeps_the_owners_own_unit_not_a_converted_one(bev):
    """The card must show 16.7 km/l when that is what was typed — never silently turn it into
    5.99 L/100km, a number the owner never entered and would not recognise."""
    _trip(bev)
    _charge(bev, kwh=20.0, cost=5.00)
    _enable(bev, l_100km=16.7, use_kml=True)
    out = db_reader.ice_comparison(db_reader.cost_per_100km())
    assert out["consumption_value"] == 16.7
    assert out["consumption_unit"] == "km/L"


def test_the_default_unit_is_l_per_100km(bev):
    _trip(bev)
    _charge(bev, kwh=20.0, cost=5.00)
    _enable(bev)
    out = db_reader.ice_comparison(db_reader.cost_per_100km())
    assert out["consumption_value"] == 6.0
    assert out["consumption_unit"] == "L/100km"


# ── the gates ─────────────────────────────────────────────────────────────────────────────────

def test_off_by_default(bev):
    _trip(bev)
    _charge(bev, kwh=20.0, cost=5.00)
    assert db_reader.ice_comparison(db_reader.cost_per_100km()) is None


def test_no_cost100_card_means_no_comparison(bev):
    """Nothing driven, nothing priced — `cost_per_100km` itself returns None, and this must not
    invent a comparison out of settings alone."""
    _enable(bev)
    assert db_reader.ice_comparison(None) is None


def test_a_zero_consumption_is_not_a_free_petrol_car(bev):
    _trip(bev)
    _charge(bev, kwh=20.0, cost=5.00)
    _enable(bev, l_100km=0.0)
    assert db_reader.ice_comparison(db_reader.cost_per_100km()) is None


def test_a_zero_fuel_price_is_treated_the_same_way(bev):
    _trip(bev)
    _charge(bev, kwh=20.0, cost=5.00)
    _enable(bev, price=0.0)
    assert db_reader.ice_comparison(db_reader.cost_per_100km()) is None


def test_the_name_is_optional(bev):
    """A blank label is fine — the card falls back to the generic wording, not to a crash."""
    _trip(bev)
    _charge(bev, kwh=20.0, cost=5.00)
    _enable(bev, name="")
    out = db_reader.ice_comparison(db_reader.cost_per_100km())
    assert out is not None and out["name"] is None


# ── no external lookup ───────────────────────────────────────────────────────────────────────

def test_there_is_no_network_call_in_it(bev):
    """The whole point of the manual-entry design: nothing here can reach the network, so there is
    nothing to mock, retry or rate-limit. Grepped from the source rather than asserted by mocking,
    so the check survives a rewrite that adds one back without anybody meaning to."""
    import inspect
    src = inspect.getsource(db_reader.ice_comparison)
    for forbidden in ("requests.", "urlopen", "http", "fetch"):
        assert forbidden not in src.lower()


# ── where it shows ───────────────────────────────────────────────────────────────────────────

def test_the_fields_live_in_settings_not_costs_or_statistics():
    """The activation switch and its two numbers are typed once, in Settings — beside every
    other on/off toggle and price in the app — never duplicated onto Costs or Statistics."""
    for key in ("ice_compare_enabled", "ice_compare_name", "ice_compare_l_100km",
                "ice_compare_fuel_price", "ice_compare_use_kml"):
        assert f'name="{key}"' in SETTINGS
        assert f'name="{key}"' not in COSTS
        assert f'name="{key}"' not in STATS


def test_settings_posts_to_the_one_endpoint():
    assert 'hx-post="api/settings/ice-compare"' in SETTINGS


def test_flipping_the_unit_converts_the_number_already_in_the_box():
    """Switching the checkbox must not silently reinterpret "5.7" under a different unit —
    it has to convert it (100 / x, symmetric either direction) so the box keeps meaning the
    same real fuel economy across the flip."""
    card = SETTINGS.split("ice_compare_use_kml", 1)[1]
    assert "100.0 / v" in card or "100 / v" in card
    assert "ice-compare-consumption-input" in card


def test_the_fields_are_stacked_not_a_cramped_row():
    """Three fields side by side in this column's narrow width squeezed the consumption box down
    to a few illegible pixels — a real tester's own screenshot showed it. One per row, always."""
    card = SETTINGS.split('hx-post="api/settings/ice-compare"', 1)[1].split(
        "</form>", 1)[0]
    assert "grid-cols-3" not in card
    assert "space-y-3" in card


def test_statistics_only_reads_the_setting_never_writes_it():
    """Statistics shows the computed result and nothing to edit — no form, no POST — so a stray
    change there can never happen; the only place a value can change is Settings."""
    assert "{% if totals.ice_compare %}" in STATS
    assert "<form" not in STATS.split("{% if totals.ice_compare %}", 1)[1].split(
        "{% endif %}", 1)[0]
    assert 'hx-post="api/settings/ice-compare"' not in STATS


def test_the_result_is_read_fresh_not_cached():
    """`db_reader.ice_comparison` is called on every request with settings read straight from the
    DB (`get_setting`, no memoisation) — so a value changed in Settings is picked up the very
    next time Statistics renders, with nothing to invalidate in between."""
    import inspect
    src = inspect.getsource(db_reader.ice_comparison)
    assert src.count("get_setting(") >= 3
    assert "cache" not in src.lower() and "memo" not in src.lower()


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_words_exist_in_every_language(path):
    d = json.loads(path.read_text(encoding="utf-8"))["translations"]
    for key in KEYS:
        assert d.get(key), f"{path.stem} is missing {key}"
