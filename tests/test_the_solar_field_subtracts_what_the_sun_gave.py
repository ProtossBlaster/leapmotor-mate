"""#272 @kingean93 — the owner types the solar kWh of a charge, Mate bills only the rest.

He has solar, a mapped wallbox, and no Home Assistant helper: `custom_kwh` (beta #13) reads the
BOUGHT kWh off an HA entity, and he has no entity to read. So he types the number instead — and
types the one he can read straight off an inverter, which is what the sun gave, not what he bought.

The two directions matter, because they are the mistake this field is shaped around: a charge of
20.0 kWh with 8.0 from the roof costs 12.0 x the home price. Type 12.0 into a field labelled Solar
and the same charge costs 8.0 x the price — cheaper, plausible, and wrong in silence. Hence a
refusal above the measured energy, and a line on the card that spells the whole subtraction out.

CI-safe: db_reader + template text, no fastapi.
"""
import sqlite3

import db as poller_db
import db_reader
import pytest

CARD = __import__("pathlib").Path("web/templates/partials/charge_card.html").read_text()


@pytest.fixture()
def home_charge(tmp_path, monkeypatch):
    """A home charge the wallbox measured at 20.0 kWh, priced at 0.24 €/kWh."""
    path = str(tmp_path / "m.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader._get.cache_clear() if hasattr(db_reader._get, "cache_clear") else None
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V')")
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
                "energy_added_kwh, ac_energy_kwh, location_type) "
                "VALUES (1, 1, '2026-08-31T08:00:00+00:00', '2026-08-31T11:00:00+00:00', "
                "20.0, 60.0, 18.0, 20.0, 'HOME')")
    con.execute("INSERT INTO settings (key, value) VALUES ('price_home_kwh', '0.24')")
    con.execute("INSERT INTO settings (key, value) VALUES ('cost_modes', "
                "'{\"HOME\": \"solar_manual\"}')")
    con.commit()
    con.close()
    return path


def _charge(cid=1):
    con = sqlite3.connect(db_reader.DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone()
    con.close()
    return row


# ── the arithmetic ──────────────────────────────────────────────────────────────
def test_the_solar_share_is_subtracted_before_pricing(home_charge):
    """20.0 measured − 8.0 solar = 12.0 billed, at 0.24 → 2.88."""
    out = db_reader.set_charge_solar_kwh(1, 8.0)
    assert out["solar_kwh"] == 8.0
    assert out["cost"] == pytest.approx(2.88)


def test_no_figure_means_the_whole_charge_is_billed(home_charge):
    """The mode is on but nobody typed a number: a charge billed in full, never an uncosted one.

    Through `update_charge_type`, not `compute_cost` directly: the caller is what resolves the
    billed basis (the wallbox delta for a HOME charge that has one), and calling the coster on its
    own bills the battery figure instead — 4.32 rather than 4.80, which is the whole distinction
    this mode rests on."""
    out = db_reader.update_charge_type(1, "HOME")
    assert out["cost"] == pytest.approx(4.80)


def test_a_charge_entirely_off_the_roof_costs_nothing(home_charge):
    """Solar equal to the measured energy is a real case, not an error — and it is exactly 0,
    not None: None would read as "no price known" and drop the charge out of the average."""
    out = db_reader.set_charge_solar_kwh(1, 20.0)
    assert out["cost"] == 0.0


# ── the mistake it exists to catch ──────────────────────────────────────────────
def test_more_solar_than_the_wallbox_measured_is_refused(home_charge):
    """Typing what you BOUGHT where the solar goes. Refused, with the ceiling named — a silent
    clamp would price the mistake as a free charge and never say so."""
    out = db_reader.set_charge_solar_kwh(1, 25.0)
    assert out["error"] == "too_much"
    assert out["max"] == pytest.approx(20.0)
    assert _charge()["solar_kwh"] is None       # nothing was written
    assert _charge()["cost"] is None


def test_zero_takes_a_wrong_number_back(home_charge):
    """Every reader tests > 0, so a stored zero reads as never-typed and the cost returns to full."""
    db_reader.set_charge_solar_kwh(1, 8.0)
    out = db_reader.set_charge_solar_kwh(1, 0.0)
    assert out["cost"] == pytest.approx(4.80)


def test_an_empty_box_changes_nothing(home_charge):
    """The field opens empty every time: an accidental open followed by Enter must be a no-op."""
    db_reader.set_charge_solar_kwh(1, 8.0)
    before = _charge()["cost"]
    db_reader.set_charge_solar_kwh(1, None)
    assert _charge()["cost"] == before
    assert _charge()["solar_kwh"] == 8.0


# ── the mode is Casa-only, like both its relatives ──────────────────────────────
def test_the_mode_is_home_only():
    assert db_reader._mode_allowed("HOME", "solar_manual") is True
    for away in ("AC", "FAST", "HPC"):
        assert db_reader._mode_allowed(away, "solar_manual") is False


# ── and the card never shows two boxes that both say "type the kWh" ─────────────
def test_the_two_fields_are_exclusive_on_the_card():
    """`show_wb` decides: it shows the solar box and hides the typed-charger one. A card carrying
    both would ask for two different numbers in two identical-looking boxes."""
    assert "{% if show_wb %}" in CARD
    assert "partials/charge_solar_kwh.html" in CARD
    assert "not (solar_mode_on() and c.location_type == 'HOME')" in CARD


def test_a_charge_the_wallbox_missed_says_so_instead_of_hiding(home_charge):
    """Option 3, Silvio 31/08: the field is per-charge, so on a charge with no meter reading it
    would simply vanish and reappear on the next one. A line says why instead."""
    assert "solar_kwh_no_meter" in CARD


def test_the_flag_and_the_mode_are_template_globals():
    """Same lesson as #222 (@ghuaywen-ai): the card is rendered by partials that build their own
    context, so a flag threaded through _ctx reaches the page and vanishes from the day drawer."""
    main = __import__("pathlib").Path("web/main.py").read_text()
    assert "solar_kwh_ok=lambda" in main
    assert "solar_mode_on=db_reader.home_prices_by_solar" in main
    # ...and it reads the one setting it needs, not the whole pricing config: this is asked once
    # per charge card, and the full read measured 455 µs against 113 µs — 13.6 ms of a month's
    # page spent re-deriving one boolean thirty times.
    assert "def home_prices_by_solar" in __import__("pathlib").Path("web/db_reader.py").read_text()
