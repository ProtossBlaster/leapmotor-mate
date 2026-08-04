"""#222 @ghuaywen-ai — the charger's own kWh, and the care a typed-in number is owed.

Mate reads what went INTO the battery. A public charger bills what came OUT of its own meter, and
the difference is real money. So the owner can type that figure in — and from there on it prices the
charge exactly as a wallbox counter does at home, while the energy Mate reports stays measured.

The whole point of these tests is the *care*, not the arithmetic:

  · it is not on screen until it is opened on purpose — a number that silently prices a charge must
    not be reachable by a stray click, or by a Tab landing in it while scrolling a day of charges;
  · it never comes pre-filled — not with the previous value, not with the measured energy — so an
    accidental open followed by Enter changes nothing;
  · there is one way in. It used to ride on the charge-TYPE form, where re-tagging a charge and
    typing this number were the same request.

Rendered, not read: the version of this file that only grepped the template would have passed with
the field wide open.
"""
import json
import pathlib
import re

import db as D
import db_reader
import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))
CARD = (TEMPLATES / "partials" / "charge_card.html").read_text()
BADGE = (TEMPLATES / "partials" / "charge_type_badge.html").read_text()
CELL = (TEMPLATES / "partials" / "charge_cost_cell.html").read_text()
MAIN = (ROOT / "web" / "main.py").read_text()


def _render(gross=None, energy=8.0, cost=None, cost_oob=False, location="AC"):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["dec"] = lambda v, n=1: f"{v:.{n}f}"
    env.filters["money"] = lambda v: f"€{v:.2f}"
    return env.get_template("partials/charge_gross_kwh.html").render(
        charge={"id": 7, "gross_kwh": gross, "energy_added_kwh": energy, "cost": cost,
                "location_type": location, "ac_energy_kwh": None},
        t=lambda k: k, cost_oob=cost_oob)


def _open_panel(html):
    """The markup from the panel's opening tag to the end of the string."""
    i = html.index('id="gk-form-7"')
    return html[i:]


# ── it has to be opened on purpose ───────────────────────────────────────────

def test_the_box_is_not_on_screen_until_it_is_opened():
    """The field exists in the markup, but inside a panel that starts hidden."""
    out = _render()
    panel = re.search(r'<div id="gk-form-7"[^>]*>', out)
    assert panel, "the panel that holds the field is gone"
    assert 'class="hidden"' in panel.group(0), \
        "the panel must start hidden — an open field gets changed by accident"
    assert out.index('name="gross_kwh"') > out.index('id="gk-form-7"'), \
        "the field must live INSIDE the panel, not before it"


def test_there_is_a_way_to_open_it():
    """A hidden panel with nothing to open it is not caution, it is a missing feature."""
    out = _render()
    assert "gk-form-7" in out.split('id="gk-form-7"')[0], "nothing on screen opens the panel"
    assert "classList.toggle('hidden')" in out


def test_nothing_but_the_pencil_shows_when_no_one_has_typed_one():
    """No value, no reading line — an offer, not an empty measurement."""
    out = _render(gross=None)
    assert "🔌" not in out.split('id="gk-form-7"')[0]


def test_what_was_typed_is_readable_without_opening_anything():
    out = _render(gross=10.0, energy=8.0)
    closed = out.split('id="gk-form-7"')[0]
    assert "10.00 kWh" in closed and "80%" in closed and "2.0 kWh" in closed


# ── and it never arrives pre-filled ──────────────────────────────────────────

def test_the_box_is_empty_even_when_a_figure_is_already_stored():
    """Re-submitting an untouched box must be a no-op, so it cannot carry the old value back in —
    and it must never suggest one either."""
    for stored in (None, 10.0):
        m = re.search(r'<input[^>]*name="gross_kwh"[^>]*>', _render(gross=stored))
        assert m, "the field is gone"
        field = m.group(0)
        assert " value=" not in field, f"pre-filled with {stored}: {field}"
        assert 'placeholder="—"' in field, "the placeholder must not suggest a number either"


def test_the_measured_energy_is_never_offered_as_a_default():
    """The two are different quantities. Seeding one with the other invents a 100%-efficient charge."""
    m = re.search(r'<input[^>]*name="gross_kwh"[^>]*>', _render(energy=8.0))
    assert m and "8.0" not in m.group(0)


# ── one way in ───────────────────────────────────────────────────────────────

def test_re_tagging_a_charge_can_no_longer_touch_it():
    """It used to ride on the charge-TYPE form: one submit, two unrelated changes."""
    assert "gross_kwh" not in BADGE
    body = MAIN.split("async def set_charge_type(", 1)[1].split("\n@app.", 1)[0]
    assert "gross_kwh" not in body


def test_it_has_its_own_endpoint():
    assert '@app.post("/api/charges/{charge_id}/gross-kwh"' in MAIN
    assert 'hx-post="api/charges/{{ charge.id }}/gross-kwh"' in \
        (TEMPLATES / "partials" / "charge_gross_kwh.html").read_text()


def test_taking_it_back_is_its_own_request():
    """Two fields named gross_kwh in one form and the empty box wins the submit — the remove button
    would quietly do nothing."""
    panel = _open_panel(_render(gross=10.0))
    assert panel.count('name="gross_kwh"') == 1
    assert 'hx-vals=\'{"gross_kwh": "0"}\'' in panel


def test_there_is_nothing_to_take_back_when_nothing_was_typed():
    assert 'hx-vals=\'{"gross_kwh": "0"}\'' not in _render(gross=None)


# ── the field is only offered where Mate has no meter of its own ─────────────

def test_it_is_not_offered_where_it_could_not_be_stored():
    """`gross_kwh_ok` is false when the database has no column for it — offering a field that
    silently swallows what you type is worse than not offering it."""
    assert "gross_kwh_ok" in CARD


def test_it_is_not_offered_on_a_wallbox_charge_or_an_untyped_one():
    assert "{% if not show_wb and c.location_type and gross_kwh_ok %}" in CARD
    assert '{% with charge=c %}{% include "partials/charge_gross_kwh.html" %}{% endwith %}' in CARD


def test_it_sits_under_the_three_tiles_and_not_inside_one():
    """Those tiles stay three columns on a phone. A panel inside the ~110 px ENERGY column unrolls
    into a thirty-line ribbon — measured at 375 px, not guessed."""
    grid = CARD.split('<div class="grid grid-cols-3 gap-2 mt-3">', 1)[1]
    tiles, after = grid.split("\n  </div>", 1)
    assert "charge_gross_kwh.html" not in tiles
    assert "charge_gross_kwh.html" in after


# ── what it does to the stored charge ────────────────────────────────────────

def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.set_setting("price_ac_kwh", "0.40")
    return pdb


def _charge(pdb, cid: int = 1, *, ctype: "str | None" = "AC"):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type) VALUES (?,1,'2026-06-02T16:48:39+00:00',"
        "'2026-06-02T18:18:36+00:00',40,52,8.0,?)", (cid, ctype))
    pdb._conn.commit()


def test_it_prices_the_charge_the_way_a_wallbox_counter_does(tmp_path, monkeypatch):
    """8 kWh reached the battery, the charger billed 10. You pay for 10."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb)
    assert db_reader.update_charge_type(1, "AC")["cost"] == 3.2          # 8 × 0.40
    out = db_reader.set_charge_gross_kwh(1, 10.0)
    assert out["gross_kwh"] == 10.0 and out["cost"] == 4.0               # 10 × 0.40


def test_what_the_car_measured_is_never_overwritten(tmp_path, monkeypatch):
    """The typed figure is a second fact about the charge, not a correction of the first one: the
    kWh the car counted into its battery stay exactly as measured."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb)
    db_reader.update_charge_type(1, "AC")
    assert db_reader.set_charge_gross_kwh(1, 10.0)["energy_added_kwh"] == 8.0


def test_a_trip_is_still_costed_on_what_reached_the_battery(tmp_path, monkeypatch):
    """The line Silvio drew on 31/07 and did NOT move on 04/08. Aligning the reported energy on the
    charger's side was a labelling decision; the blend is physics — a trip consumes what is in the
    pack, so the € it carries divides by that. Typing 10 kWh on an 8 kWh charge must make the
    battery dearer per kWh, not cheaper."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb)
    db_reader.update_charge_type(1, "AC")
    src = (ROOT / "web" / "db_reader.py").read_text()
    blend = src.split("def _wac_blend(", 1)[1].split("\ndef ", 1)[0]
    assert "gross_kwh" not in blend.split('"""')[-1], "the blend must divide by the battery energy"
    db_reader.set_charge_gross_kwh(1, 10.0)
    assert db_reader.blended_price_at(1, "2026-07-01T00:00:00+00:00") == pytest.approx(4.0 / 8.0)


def test_an_empty_box_leaves_the_stored_figure_alone(tmp_path, monkeypatch):
    """The one that makes an accidental open harmless."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb)
    db_reader.set_charge_gross_kwh(1, 10.0)
    out = db_reader.set_charge_gross_kwh(1, None)
    assert out["gross_kwh"] == 10.0 and out["cost"] == 4.0


def test_an_empty_box_does_not_re_price_an_old_charge_at_todays_tariff(tmp_path, monkeypatch):
    """The one the first version of this file missed: keeping the stored figure is not enough.

    A charge's cost is frozen the moment it is priced — tariffs change, history does not. Handing an
    empty box to update_charge_type would recompute it at TODAY's price, so opening the panel on a
    two-year-old charge and pressing Enter would quietly rewrite what it cost."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb)
    assert db_reader.update_charge_type(1, "AC")["cost"] == 3.2      # priced at 0.40
    db_reader.set_setting("price_ac_kwh", "0.90")                    # the tariff has since changed
    assert db_reader.set_charge_gross_kwh(1, None)["cost"] == 3.2


def test_a_zero_takes_it_back_and_the_cost_returns_to_the_measured_basis(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb)
    db_reader.set_charge_gross_kwh(1, 10.0)
    out = db_reader.set_charge_gross_kwh(1, 0.0)
    assert not out["gross_kwh"] and out["cost"] == 3.2


def test_it_cannot_type_a_charge_nobody_has_typed(tmp_path, monkeypatch):
    """The field is only offered on a typed charge; a stray request must not tag one as a side
    effect of carrying a number."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, ctype=None)
    db_reader.set_charge_gross_kwh(1, 10.0)
    row = pdb._conn.execute("SELECT location_type, gross_kwh FROM charges WHERE id=1").fetchone()
    assert row["location_type"] is None and not row["gross_kwh"]


def test_a_typo_is_read_as_empty_not_as_a_zero():
    """'1O.5' must leave a good figure standing, not wipe it."""
    body = MAIN.split("async def set_charge_gross_kwh(", 1)[1].split("\n@app.", 1)[0]
    assert "except (ValueError, TypeError):" in body and "gross_kwh = None" in body
    assert 'min(float(_g), 500.0)' in body and 'max(0.0,' in body


# ── the price per kWh must agree with the price ──────────────────────────────

def test_the_cost_cell_is_written_once():
    """It was drawn in three places, each free to disagree about what the charge was billed on."""
    assert CELL.count("_billed") >= 2
    for tpl in (CARD, BADGE):
        assert 'include "partials/charge_cost_cell.html"' in tpl
        assert "/kWh</div>" not in tpl, "a second copy of the cost cell has come back"


def test_the_price_per_kwh_divides_by_the_energy_mate_shows():
    """It mirrors _billed_kwh() — the declared single source of truth for the energy shown for a
    charge — because the period totals, get_charge_stats and the calendar divide by that same rule.
    Same three branches, same order, or this card prints a €/kWh no other page agrees with."""
    assert "charge.ac_energy_kwh if (charge.location_type == 'HOME' and charge.ac_energy_kwh)" in CELL
    assert "charge.gross_kwh if charge.gross_kwh else charge.energy_added_kwh" in CELL
    body = (ROOT / "web" / "db_reader.py").read_text()
    billed = body.split("def _billed_kwh(", 1)[1].split("\ndef ", 1)[0]
    for branch in ("ac_energy_kwh", "gross_kwh", "energy_added_kwh"):
        assert branch in billed and branch in CELL, f"{branch} missing on one side of the mirror"


def test_the_cost_is_refreshed_when_the_figure_changes():
    """It is the billing basis: leaving the price cell stale would show two numbers that disagree."""
    assert 'hx-swap-oob="true"' in _render(gross=10.0, cost=4.0, cost_oob=True)
    assert '"cost_oob": True' in MAIN.split("async def set_charge_gross_kwh(", 1)[1].split("\n@app.", 1)[0]


def test_the_card_does_not_emit_the_out_of_band_copy():
    assert 'hx-swap-oob' not in _render(gross=10.0, cost=4.0, cost_oob=False)


# ── words ────────────────────────────────────────────────────────────────────

def test_there_are_locales_to_check():
    assert len(LOCALES) >= 6, [p.name for p in LOCALES]


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_every_word_the_panel_uses_exists(path):
    d = json.loads(path.read_text())["translations"]
    for key in ("gross_kwh_short", "gross_kwh_lost", "gross_kwh_help", "efficiency",
                "cancel", "band_remove"):
        assert d.get(key), f"{path.stem} is missing {key}"
