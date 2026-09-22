"""The energy of a charge, told the same way on every screen.

A charge has two energies: what the charger delivered (the wallbox counter at home, the charger's
own display typed in elsewhere — the basis of the cost) and what reached the battery (from the SoC
difference). The charge card has said both for a long time, in one fixed shape: at home with a meter
the counter leads, "in battery · efficiency" under it; elsewhere the battery leads and a typed figure
stands on its own line. The Overview's Last-charge tile said only the battery figure, beside a cost
computed on the other one — a €/kWh that exists nowhere else.

The rule that decides which figure leads lived inline in the card's Jinja, in two partials. It is
one helper now, `charge_energy_view`, and the card, its gross line and the Overview tile read from
it. Pinned here: the helper's truth table, the card's render before and after (word for word, from
the render on `main` before the move), and the tile.
"""
import json
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"


# ── the helper's truth table ───────────────────────────────────────────────────

def _home(ac, dc, gross=None):
    return {"location_type": "HOME", "ac_energy_kwh": ac, "energy_added_kwh": dc, "gross_kwh": gross}


def _public(dc, gross=None):
    return {"location_type": "AC", "ac_energy_kwh": None, "energy_added_kwh": dc, "gross_kwh": gross}


def test_at_home_with_a_meter_the_counter_leads_and_the_battery_stands_under_it():
    e = db_reader.charge_energy_view(_home(14.87, 12.6))
    assert (e["headline"], e["headline_kwh"], e["battery_kwh"], e["gross_kwh"]) == ("wallbox", 14.87, 12.6, None)
    assert e["wallbox_eff"] == 84.7
    assert e["gross_eff"] is None and e["gross_lost_kwh"] is None


def test_the_efficiency_is_the_one_definition_the_wallbox_page_reads():
    """#295 made `charge_efficiency` the only place the ratio is computed and withheld; the card
    reads it, so a second copy here would be how the two screens drift apart again."""
    for ac, dc in ((25.0, 21.37), (14.87, 12.6), (7.69, 10.03), (10.0, 10.0)):
        assert (db_reader.charge_energy_view(_home(ac, dc))["wallbox_eff"]
                == db_reader.charge_efficiency(ac, dc))


@pytest.mark.parametrize("ac, dc", [(25.0, 30.0), (25.0, 25.02)])
def test_an_efficiency_above_100_is_not_shown(ac, dc):
    """A ΔSoC figure above the meter's is the BMS snapping to full, not physics."""
    e = db_reader.charge_energy_view(_home(ac, dc))
    assert e["headline"] == "wallbox" and e["wallbox_eff"] is None


def test_an_efficiency_of_exactly_100_is_still_shown_at_home():
    """Kept as the card had it: `<= 100` there, `lost > 0` on the gross line. Levelling the two is
    a separate change; this pins that the move changed nothing."""
    assert db_reader.charge_energy_view(_home(20.0, 20.0))["wallbox_eff"] == 100.0


@pytest.mark.parametrize("dc", [0, None])
def test_a_meter_reading_with_no_battery_figure_still_leads(dc):
    e = db_reader.charge_energy_view(_home(14.87, dc))
    assert (e["headline"], e["headline_kwh"], e["battery_kwh"], e["wallbox_eff"]) == ("wallbox", 14.87, 0, None)


def test_a_typed_figure_stands_beside_the_battery_one_with_the_loss():
    e = db_reader.charge_energy_view(_public(37.6, gross=41.5))
    assert (e["headline"], e["headline_kwh"], e["gross_kwh"]) == ("battery", 37.6, 41.5)
    assert e["gross_eff"] == pytest.approx(90.6, abs=0.01)
    assert e["gross_lost_kwh"] == pytest.approx(3.9)
    assert e["wallbox_eff"] is None


def test_a_typed_figure_with_no_battery_figure_is_shown_without_an_efficiency():
    e = db_reader.charge_energy_view(_public(None, gross=41.5))
    assert (e["headline_kwh"], e["gross_kwh"], e["gross_eff"], e["gross_lost_kwh"]) == (0, 41.5, None, None)


def test_a_typed_figure_equal_to_the_battery_one_shows_no_efficiency():
    e = db_reader.charge_energy_view(_public(30.0, gross=30.0))
    assert e["gross_kwh"] == 30.0 and e["gross_eff"] is None and e["gross_lost_kwh"] is None


def test_a_battery_figure_alone_is_just_that():
    e = db_reader.charge_energy_view(_public(20.0))
    assert e == {"headline_kwh": 20.0, "headline": "battery", "battery_kwh": 20.0, "wallbox_eff": None,
                 "gross_kwh": None, "gross_eff": None, "gross_lost_kwh": None}


def test_the_meter_leads_over_a_typed_figure_at_home_and_the_typed_figure_is_kept():
    """Same order as `_billed_kwh`: the thing that billed you is the thing that delivered. The typed
    figure is still reported, with its own efficiency: the field that holds it may be open on
    screen (typed, then re-tagged to Home), and it must keep saying what it holds. Whether to
    OFFER the field is the card's decision, not the helper's."""
    e = db_reader.charge_energy_view(_home(14.87, 12.6, gross=15.0))
    assert e["headline"] == "wallbox" and e["headline_kwh"] == 14.87
    assert e["gross_kwh"] == 15.0 and e["gross_eff"] == pytest.approx(84.0) \
        and e["gross_lost_kwh"] == pytest.approx(2.4)


def test_a_meter_reading_of_zero_does_not_lead():
    e = db_reader.charge_energy_view(_home(0, 12.6))
    assert e["headline"] == "battery" and e["headline_kwh"] == 12.6


def test_the_card_can_reach_it_from_every_context_it_is_rendered_in():
    """The card is rendered by the page and by two partials that build their context by hand — a
    value threaded through `_ctx` reaches the page and vanishes from the day drawer. A global."""
    main = (ROOT / "web" / "main.py").read_text()
    assert "charge_energy=db_reader.charge_energy_view," in main


# ── the pages, rendered ────────────────────────────────────────────────────────

@pytest.fixture
def mate(tmp_path, monkeypatch):
    """A real Mate over a database of our own. The pages are asked through the app, not the
    partial alone: the card's energy tile depends on a template GLOBAL, and a test environment
    of its own would pass with the page broken."""
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    import main
    from starlette.testclient import TestClient
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    pdb._conn.commit()
    monkeypatch.setattr(db_reader, "_lang_memo", [None])
    return pdb, TestClient(main.app)


def _charge(pdb, cid, day, batt, *, ac=None, gross=None, ctype="HOME", cost=None):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, gross_kwh, location_type, cost)"
        f" VALUES (?,1,'2026-07-{day:02d}T09:00:00+00:00','2026-07-{day:02d}T11:00:00+00:00',"
        "20,70,?,?,?,?,?)", (cid, batt, ac, gross, ctype, cost))
    pdb._conn.commit()


def _day(client, day):
    """One day's cards, the way the Month view's drawer loads them."""
    return client.get(f"/api/charges/calendar/day?year=2026&month=7&day={day}").text


def _energy_tile(html):
    """The card's ENERGY tile, whitespace collapsed — from the Stats comment to the next tile."""
    i = html.index("<!-- Stats -->")
    j = html.index('<div style="background:#0f172a;border-radius:8px;padding:8px 12px">', i + 200)
    return " ".join(html[i:j].split())


# The tile as the card rendered it on v3.17.4, before the rule moved into the helper. Word for word:
# the move is a refactor, and a refactor that changes the page is not one.
_TILE_HOME_METER = (
    '<!-- Stats --> <div class="grid grid-cols-3 gap-2 mt-3"> '
    '<div style="background:#0f172a;border-radius:8px;padding:8px 12px"> '
    '<div class="stat-label" style="font-size:10px">Energy</div> '
    '<div style="font-size:16px;font-weight:700;color:#fbbf24"> '
    '+14.9<span style="font-size:11px;color:#94a3b8;font-weight:400"> kWh</span> '
    '<span style="font-size:10px;color:#60a5fa;font-weight:400" title="Home charges are billed on the '
    'energy the wallbox drew (AC, conversion losses included), never less than what reached the '
    'battery."> 🔌 wallbox (billed)</span> </div> '
    '<div style="font-size:10px;color:#94a3b8;margin-top:3px" title="Home charges are billed on the '
    'energy the wallbox drew (AC, conversion losses included), never less than what reached the '
    'battery."> 🔋 12.6 kWh In battery (DC) · efficiency 85% </div> </div>')
_BATTERY_HELP = ('The energy that actually entered the battery (DC). What you draw from the grid is '
                 '~10–15% higher (AC→DC conversion losses); without a wallbox reading, Mate can only '
                 'show this figure.')
_TILE_BATTERY = (
    '<!-- Stats --> <div class="grid grid-cols-3 gap-2 mt-3"> '
    '<div style="background:#0f172a;border-radius:8px;padding:8px 12px"> '
    '<div class="stat-label" style="font-size:10px">Energy</div> '
    '<div style="font-size:16px;font-weight:700;color:#fbbf24"> '
    '+{kwh}<span style="font-size:11px;color:#94a3b8;font-weight:400"> kWh</span> '
    '<span style="font-size:10px;color:#94a3b8;font-weight:400" title="' + _BATTERY_HELP + '"> '
    '🔋 In battery (DC)</span> </div> </div>')
_GROSS_LINE = (
    '<span style="font-size:10px;color:#94a3b8" title="What the charger&#39;s display said: the gross '
    'kWh, conversion losses included. You type it, because Mate has no meter on a public charger. It '
    'becomes the charge&#39;s energy, and the cost is computed on it.">🔌 41.50 kWh delivered by the '
    'charger · efficiency 91% (lost 3.9 kWh)</span>')


def test_the_card_at_home_with_a_meter_renders_as_before(mate):
    pdb, client = mate
    _charge(pdb, 1, 3, 12.6, ac=14.87, cost=8.71)
    html = _day(client, 3)
    assert _energy_tile(html) == _TILE_HOME_METER
    assert 'id="gk-form-1"' not in html, "a metered home charge offers no gross field"


def test_the_card_with_a_typed_figure_renders_as_before(mate):
    pdb, client = mate
    _charge(pdb, 2, 4, 37.6, gross=41.5, ctype="AC")
    html = _day(client, 4)
    assert _energy_tile(html) == _TILE_BATTERY.format(kwh="37.6")
    assert _GROSS_LINE in " ".join(html.split())
    assert 'id="gk-form-2"' in html


def test_the_card_with_the_battery_figure_alone_renders_as_before(mate):
    pdb, client = mate
    _charge(pdb, 3, 5, 20.0, ctype="AC")
    html = _day(client, 5)
    assert _energy_tile(html) == _TILE_BATTERY.format(kwh="20.0")
    closed = html.split('id="gk-3"', 1)[1].split('id="gk-form-3"', 1)[0]
    assert "🔌" not in closed, "nothing typed, nothing to read — only the way in"


def test_under_solar_pricing_a_metered_home_charge_offers_the_solar_field_not_the_gross_one(mate):
    """#272: the two fields are exclusive by construction, keyed on the same decision the helper
    now makes. The helper knows nothing of solar mode; the card still does."""
    pdb, client = mate
    db_reader.set_setting("cost_modes", json.dumps({"HOME": "solar_manual"}))
    _charge(pdb, 1, 3, 12.6, ac=14.87, cost=8.71)
    html = _day(client, 3)
    assert _energy_tile(html) == _TILE_HOME_METER
    assert 'id="sk-1"' in html and 'id="gk-form-1"' not in html


def test_under_solar_pricing_an_unmetered_home_charge_says_so_and_offers_neither_field(mate):
    pdb, client = mate
    db_reader.set_setting("cost_modes", json.dumps({"HOME": "solar_manual"}))
    _charge(pdb, 4, 6, 12.6, ctype="HOME")
    html = _day(client, 6)
    assert _energy_tile(html) == _TILE_BATTERY.format(kwh="12.6")
    assert "the wallbox did not measure this charge" in html
    assert 'id="sk-4"' not in html and 'id="gk-form-4"' not in html


def test_a_typed_figure_survives_being_re_tagged_to_home(mate):
    """A charge the wallbox measured, typed as AC, gets the charger's figure typed in; the owner
    then re-tags it to Home. Re-tagging refreshes only the badge and the cost, so the gross box is
    still on screen — and saving it again must answer with what it holds and the way to take it
    back, not with an empty offer. That is what the stored template did; the first version of the
    helper hid both, because it dropped the typed figure whenever the counter led."""
    pdb, client = mate
    _charge(pdb, 1, 3, 12.6, ac=14.87, ctype="AC")
    first = client.post("/api/charges/1/gross-kwh", data={"gross_kwh": "16"}).text
    assert "16.00 kWh delivered by the charger · efficiency 79% (lost 3.4 kWh)" in first
    assert client.post("/api/charges/1/type", data={"location_type": "HOME"}).status_code == 200
    again = client.post("/api/charges/1/gross-kwh", data={"gross_kwh": "16"}).text
    assert "16.00 kWh delivered by the charger · efficiency 79% (lost 3.4 kWh)" in again
    assert 'hx-vals=\'{"gross_kwh": "0"}\'' in again, "the way to take the figure back is gone"


# ── the rule lives in one place ────────────────────────────────────────────────

@pytest.mark.parametrize("rel", ["partials/charge_card.html", "partials/charge_gross_kwh.html"])
def test_no_template_computes_the_energy_on_its_own(rel):
    """The rule (which figure leads, whether the efficiency is shown) was inline in two partials
    and drifted between them at exactly 100 %. A template that divides the two columns or tests the
    meter itself is a third copy."""
    src = (TEMPLATES / rel).read_text()
    assert "charge_energy(" in src
    assert "energy_added_kwh /" not in src
    assert "ac_energy_kwh and" not in src
