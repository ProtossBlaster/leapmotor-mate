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
    # The unrounded ratio, because the card and the tile each round once for display (dec(0) → 85).
    # This pinned 84.7 while `charge_efficiency` still rounded to a tenth first — the very defect
    # @arekm reported on v3.17.4, fixed in de255e8: rounding before the 100 % check moved the check.
    assert e["wallbox_eff"] == pytest.approx(100 * 12.6 / 14.87)
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
                 "gross_kwh": None, "gross_eff": None, "gross_lost_kwh": None, "has_home_meter": False}


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
    db_reader.set_setting("setup_complete", "1")     # or "/" is the setup wizard, not the Overview
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


# ── the Overview's Last-charge tile ────────────────────────────────────────────

def _last_charge_tile(html):
    i = html.index("<!-- Last charge -->")
    return " ".join(html[i:html.index("</div>\n  </div>", i)].split())


def test_the_tile_at_home_with_a_meter_says_what_was_billed_and_what_reached_the_battery(mate):
    pdb, client = mate
    _charge(pdb, 1, 3, 12.6, ac=14.87, cost=8.71)
    tile = _last_charge_tile(client.get("/").text)
    assert "+14.9 kWh" in tile and "🔌 wallbox (billed)" in tile
    assert "🔋 12.6 kWh In battery (DC) · efficiency 85%" in tile
    assert ">Energy<" in tile and "Energy added" not in tile


def test_the_tile_and_the_card_print_the_same_efficiency(mate):
    """Both read `charge_efficiency` through the helper and print it with dec(0) — AC 25 / DC 21.37
    is a charge where two roundings of the same ratio could disagree."""
    pdb, client = mate
    _charge(pdb, 1, 3, 21.37, ac=25.0)
    shown = f"efficiency {db_reader.charge_efficiency(25.0, 21.37):.0f}%"
    assert shown in _last_charge_tile(client.get("/").text)
    assert shown in _energy_tile(_day(client, 3))


def test_the_tile_with_a_typed_figure_says_it_on_its_own_line(mate):
    pdb, client = mate
    _charge(pdb, 2, 4, 37.6, gross=41.5, ctype="AC")
    tile = _last_charge_tile(client.get("/").text)
    assert "+37.6 kWh" in tile and "🔋 In battery (DC)" in tile
    assert "🔌 41.50 kWh delivered by the charger · efficiency 91% (lost 3.9 kWh)" in tile
    assert "wallbox (billed)" not in tile


def test_the_tile_with_the_battery_figure_alone_says_just_that(mate):
    pdb, client = mate
    _charge(pdb, 3, 5, 20.0, ctype="AC")
    tile = _last_charge_tile(client.get("/").text)
    assert "+20.0 kWh" in tile and "🔋 In battery (DC)" in tile
    assert "efficiency" not in tile and "delivered by the charger" not in tile


def test_the_tile_has_no_pencil():
    """Typing the charger's figure stays on the Charges page, beside the charge it belongs to."""
    src = (TEMPLATES / "overview.html").read_text()
    assert "charge_gross_kwh.html" not in src and "✎" not in src.split("<!-- Last charge -->")[1].split("{% else %}")[0]


def test_an_empty_database_still_renders_the_overview(mate):
    _, client = mate
    r = client.get("/")
    assert r.status_code == 200 and "No charges recorded yet" in r.text


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

@pytest.mark.parametrize("rel", ["partials/charge_card.html", "partials/charge_gross_kwh.html",
                                 "overview.html"])
def test_no_template_computes_the_energy_on_its_own(rel):
    """The rule (which figure leads, whether the efficiency is shown) was inline in two partials
    and drifted between them at exactly 100 %. A template that divides the two columns or tests the
    meter itself is a third copy."""
    src = (TEMPLATES / rel).read_text()
    assert "charge_energy(" in src
    assert "energy_added_kwh /" not in src
    assert "ac_energy_kwh and" not in src


# ── a merged charge leads with what it bills ───────────────────────────────────

def _merged(pdb, parent, child, day, batt_parent, batt_child, *, gross=None, ac=None, ctype="AC",
            cost=None):
    """One plug-in the car reported in two pieces, merged the way the owner does it: a meter
    reading (`ac`) on the FIRST piece only — the poller drops an implausible one, and the second
    piece is left with the battery — and a figure (`gross`) typed on the merged card afterwards,
    so it is the whole plug-in's."""
    _charge(pdb, parent, day, batt_parent, ac=ac, ctype=ctype, cost=cost)
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type)"
        f" VALUES (?,1,'2026-07-{day:02d}T11:10:00+00:00','2026-07-{day:02d}T12:00:00+00:00',"
        "70,80,?,?)", (child, batt_child, ctype))
    pdb._conn.commit()
    assert db_reader.merge_charges(parent, child)["ok"]
    if gross is not None:
        db_reader.set_charge_gross_kwh(parent, gross)


def _the_charge():
    return db_reader.get_charges(limit=5)[0]


def test_a_merged_charge_the_meter_measured_throughout_leads_with_the_counter(mate):
    """12 + 6 on the meter for 10 + 5 in the battery: the counter, 18, as on one charge."""
    pdb, _ = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, ac=12.0, ctype="HOME")
    pdb._conn.execute("UPDATE charges SET ac_energy_kwh=6.0 WHERE id=2")
    pdb._conn.commit()
    e = db_reader.charge_energy_view(_the_charge())
    assert (e["headline"], e["headline_kwh"], e["battery_kwh"]) == ("wallbox", 18.0, 15.0)
    assert e["wallbox_eff"] == db_reader.charge_efficiency(18.0, 15.0)


def test_a_merged_charge_the_meter_half_measured_leads_with_what_it_bills(mate):
    """The meter caught the first piece (12) and not the second: 17 is billed — 12 from the meter,
    5 from the battery — and 17 leads, under the totals' word for that sum. On the group's summed
    columns it read as "HOME with a counter": "12.0 kWh wallbox (billed)" over the cost of 17."""
    pdb, _ = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, ac=12.0, ctype="HOME", cost=3.4)
    e = db_reader.charge_energy_view(_the_charge())
    assert (e["headline"], e["headline_kwh"], e["battery_kwh"]) == ("delivered", 17.0, 15.0)
    assert e["wallbox_eff"] is None and e["gross_kwh"] is None


def test_a_figure_typed_on_the_merged_card_keeps_the_cards_usual_shape(mate):
    """Typed for every piece, it is the billed figure, and the card says it the way it says a typed
    figure on any charge: the battery figure leads, the typed one on its own line."""
    pdb, _ = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, gross=30.0)
    e = db_reader.charge_energy_view(_the_charge())
    assert (e["headline"], e["headline_kwh"], e["battery_kwh"]) == ("battery", 15.0, 15.0)
    assert (e["gross_kwh"], e["gross_eff"], e["gross_lost_kwh"]) == (30.0, 50.0, 15.0)


def test_a_session_merged_in_after_the_figure_was_typed_shows_beside_it(mate):
    """30 typed for two pieces, a third session of 5 kWh merged in afterwards: 35 is billed and 35
    leads; the typed figure stays on its line, as typed."""
    pdb, _ = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, gross=30.0)
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type) VALUES"
        " (3,1,'2026-07-03T12:10:00+00:00','2026-07-03T13:00:00+00:00',80,90,5.0,'AC')")
    pdb._conn.commit()
    assert db_reader.merge_charges(1, 3)["ok"]
    e = db_reader.charge_energy_view(_the_charge())
    assert (e["headline"], e["headline_kwh"], e["battery_kwh"]) == ("delivered", 35.0, 20.0)
    assert e["gross_kwh"] == 30.0
    assert e["gross_eff"] is None and e["gross_lost_kwh"] is None
    html = _day(mate[1], 3)
    gross_line = html.split('id="gk-1"')[1].split('id="gk-form-1"')[0]
    assert "30.00 kWh delivered by the charger" in gross_line
    assert "efficiency" not in gross_line and "lost" not in gross_line


def test_separate_gross_readings_covering_every_piece_have_a_combined_efficiency(mate):
    pdb, client = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0)
    assert db_reader.unmerge_charges(1)["ok"]
    db_reader.set_charge_gross_kwh(1, 12.0)
    db_reader.set_charge_gross_kwh(2, 6.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    e = db_reader.charge_energy_view(_the_charge())
    assert e["gross_kwh"] == 18.0
    assert e["gross_eff"] == pytest.approx(100 * 15 / 18)
    assert e["gross_lost_kwh"] == 3.0
    assert "18.00 kWh delivered by the charger · efficiency 83% (lost 3.0 kWh)" in _day(client, 3)


def test_the_card_and_overview_show_only_the_gross_reading_in_effect(mate):
    pdb, client = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0)
    assert db_reader.unmerge_charges(1)["ok"]
    db_reader.set_charge_gross_kwh(2, 12.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 17.0)
    for html in (_day(client, 3), _last_charge_tile(client.get("/").text)):
        assert "17.00 kWh delivered by the charger · efficiency 88% (lost 2.0 kWh)" in html
        assert "29.00 kWh" not in html


@pytest.mark.parametrize("solar_kwh", [0.0, 2.0])
def test_a_partial_home_meter_keeps_its_solar_editor(mate, solar_kwh):
    pdb, client = mate
    db_reader.set_setting("cost_modes", json.dumps({"HOME": "solar_manual"}))
    _merged(pdb, 1, 2, 3, 10.0, 5.0, ac=12.0, ctype="HOME")
    db_reader.set_charge_solar_kwh(1, solar_kwh)
    html = _day(client, 3)
    assert "+17.0<span" in _energy_tile(html)
    assert 'id="sk-form-1"' in html and 'id="gk-form-1"' not in html
    assert "the wallbox did not measure this charge" not in html
    if solar_kwh:
        assert 'hx-vals=\'{"solar_kwh": "0"}\'' in html
        response = client.post("/api/charges/1/solar-kwh", data={"solar_kwh": "0"})
        assert response.status_code == 200
        assert db_reader.get_charge(1)["solar_kwh"] == 0.0
        assert 'id="sk-form-1"' in _day(client, 3)


def test_a_partial_home_meter_does_not_offer_a_new_gross_edit(mate):
    pdb, client = mate
    db_reader.set_setting("price_home_kwh", "0.20")
    _merged(pdb, 1, 2, 3, 10.0, 5.0, ac=12.0, ctype="HOME")
    db_reader.update_charge_type(1, "HOME")
    html = _day(client, 3)
    assert "+17.0<span" in _energy_tile(html)
    assert 'id="gk-form-1"' not in html and 'id="sk-form-1"' not in html
    assert db_reader.get_charge_stats()["total_cost"] == 3.4


def test_a_single_charge_never_takes_that_variant():
    """Its billed figure IS the counter, the typed figure or the battery one."""
    for c in (_home(14.87, 12.6), _home(0, 12.6, 16.0), _public(37.6, 41.5), _public(20.0)):
        assert db_reader.charge_energy_view(c)["headline"] in ("wallbox", "battery")


def test_the_card_of_a_half_measured_merged_charge_says_delivered(mate):
    pdb, client = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, ac=12.0, ctype="HOME", cost=3.4)
    tile = _energy_tile(_day(client, 3))
    assert "+17.0<span" in tile and "> 🔌 delivered</span>" in tile
    assert "🔋 15.0 kWh In battery (DC) </div>" in tile
    assert "wallbox (billed)" not in tile and "efficiency" not in tile


def test_the_tile_of_a_half_measured_merged_charge_says_the_same(mate):
    pdb, client = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, ac=12.0, ctype="HOME", cost=3.4)
    tile = _last_charge_tile(client.get("/").text)
    assert "+17.0 kWh" in tile and "🔌 delivered</span>" in tile
    assert "🔋 15.0 kWh In battery (DC)" in tile
    assert "wallbox (billed)" not in tile and "efficiency" not in tile


def _price_per_kwh(html, charge_id):
    cell = html[html.index(f'id="cost-{charge_id}"'):]
    return " ".join(cell[:cell.index("/kWh")].split()).rsplit(">", 1)[1]


def test_the_price_per_kwh_on_the_card_is_the_one_the_totals_give(mate):
    """12 typed on the first piece's card at 0.45 (5.40), the second piece 5 kWh at 0.45 (2.25),
    then merged: 7.65 for 17 — 0.45 on the Charges page's summary, and 0.45 on the card. The card
    carried its own copy of the rule, for one row, and divided by 12: 0.64."""
    pdb, client = mate
    db_reader.set_setting("price_ac_kwh", "0.45")
    _charge(pdb, 1, 3, 10.0, ctype="AC")
    db_reader.set_charge_gross_kwh(1, 12.0)          # priced on it: 5.40
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type, cost) VALUES"
        " (2,1,'2026-07-03T11:10:00+00:00','2026-07-03T12:00:00+00:00',70,80,5.0,'AC',2.25)")
    pdb._conn.commit()
    assert db_reader.merge_charges(1, 2)["ok"]
    s = db_reader.get_charge_stats()
    assert (s["total_cost"], s["priced_kwh"], s["avg_price"]) == (7.65, 17.0, 0.45)
    assert _price_per_kwh(_day(client, 3), 1) == "0.45 €"


# ── the totals: "delivered", with the battery figure beside it ─────────────────

def _tile(html, label):
    """One summary tile's markup, whitespace collapsed: from its label to the next tile."""
    i = html.index(">" + label + "<")
    j = html.find('text-align:center"', i)
    return " ".join(html[i:j if j > 0 else i + 1500].split())


def test_the_charges_total_carries_the_battery_figure(mate):
    pdb, _ = mate
    _charge(pdb, 1, 3, 27.5, ac=30.0, cost=9.0)
    _charge(pdb, 2, 9, 37.6, gross=41.5, ctype="AC", cost=20.0)
    _charge(pdb, 3, 11, 20.0, ctype="AC")
    _charge(pdb, 4, 15, 20.0, ac=22.0, gross=25.0, cost=7.0)
    s = db_reader.get_charge_stats()
    assert (s["total_kwh"], s["battery_kwh"]) == (113.5, 105.1)


def test_a_merged_charge_with_a_typed_figure_is_delivered_once_and_in_battery_on_every_piece(mate):
    """10 + 5 kWh in the battery, 30 typed for the whole plug-in: 30 delivered, 15 in battery —
    the pair the card and the calendar show. Summed row by row it came out as 35."""
    pdb, _ = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, gross=30.0)
    s = db_reader.get_charge_stats()
    assert (s["total_kwh"], s["battery_kwh"]) == (30.0, 15.0)
    assert db_reader.get_charges_calendar_month(2026, 7)["total"] | {"kwh": 30.0, "battery_kwh": 15.0} \
        == db_reader.get_charges_calendar_month(2026, 7)["total"]


def test_a_merged_home_charge_the_meter_only_half_measured_is_delivered_piece_by_piece(mate):
    """12 kWh on the meter for the first piece, none for the second, 10 + 5 in the battery: 17
    delivered and 15 in battery, as before the merge. The group's summed columns said 12."""
    pdb, _ = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, ac=12.0, ctype="HOME", cost=3.4)
    s = db_reader.get_charge_stats()
    assert (s["total_kwh"], s["battery_kwh"]) == (17.0, 15.0)


@pytest.mark.parametrize("lang, total, delivered, in_battery", [
    ("en", "71.5", "delivered", "65.1 kWh in battery"),
    ("pl", "71,5", "dostarczone", "65,1 kWh w baterii")])
def test_the_charges_page_says_delivered_and_in_battery(mate, lang, total, delivered, in_battery):
    """Rendered through the app, in two languages: the macro takes the translator as an argument
    because an imported macro does not see the render context, and the wrong way to fix that
    passes in English and raises UndefinedError everywhere else.

    The word sits UNDER the number, not in its unit: with a four-digit total, "kWh delivered"
    broke onto a second line in a ~170 px tile, on a phone and in the desktop grid alike."""
    pdb, client = mate
    db_reader.set_setting("language", lang)
    _charge(pdb, 1, 3, 27.5, ac=30.0, cost=9.0)
    _charge(pdb, 2, 9, 37.6, gross=41.5, ctype="AC")
    html = client.get("/charges").text
    tile = _tile(html, {"en": "Total energy", "pl": "Łączna energia"}[lang])
    assert f"{total}<span" in tile and "> kWh</span>" in tile, \
        "both figures of the pair take the reader's decimal separator"
    assert f">{delivered}</span> · <span title=" in tile and f"{in_battery}</span>" in tile, \
        "the macro's markup must reach the page as HTML, not escaped"
    assert "&lt;span" not in tile


def test_the_charges_page_hides_the_battery_figure_when_it_would_repeat_the_total(mate):
    pdb, client = mate
    _charge(pdb, 3, 11, 20.0, ctype="AC")
    _charge(pdb, 4, 12, 15.0, ctype="AC")
    tile = _tile(client.get("/charges").text, "Total energy")
    assert "35<span" in tile and ">delivered</span>" in tile
    assert "in battery" not in tile and " · " not in tile


def _four_kinds(pdb):
    """One charge of each kind, and the meter beating a typed figure at home."""
    _charge(pdb, 1, 3, 27.5, ac=30.0, cost=9.0)
    _charge(pdb, 2, 9, 37.6, gross=41.5, ctype="AC", cost=20.0)
    _charge(pdb, 3, 11, 20.0, ctype="AC")
    _charge(pdb, 4, 15, 20.0, ac=22.0, gross=25.0, cost=7.0)


def test_the_statistics_total_is_the_billed_energy_with_the_battery_figure_beside_it(mate):
    """Energy Charged summed the battery column alone — the one total in Mate computed by a rule
    of its own. It is `_billed_kwh` now, like the Charges page, the month strip and the search."""
    pdb, _ = mate
    _four_kinds(pdb)
    rows = db_reader.get_charges(limit=1_000_000)
    s = db_reader.get_stats_summary()
    assert s["total_kwh_charged"] == round(sum(db_reader._billed_kwh(r) for r in rows), 2) == 113.5
    assert s["total_kwh_charged"] == db_reader.get_charge_stats()["total_kwh"]
    assert s["total_kwh_battery"] == 105.1


@pytest.mark.parametrize("kw, delivered", [
    ({"gross": 30.0}, 30.0),                       # typed for the whole plug-in: once
    ({"ac": 12.0, "ctype": "HOME"}, 17.0),         # metered on one piece only: piece by piece
    ({"ac": 12.0, "ctype": "HOME", "gross": 30.0}, 30.0),   # …and the typed figure covers the rest
])
def test_the_statistics_total_reads_a_merged_charge_like_the_charges_page(mate, kw, delivered):
    pdb, _ = mate
    _merged(pdb, 1, 2, 3, 10.0, 5.0, **kw)
    s = db_reader.get_stats_summary()
    assert (s["total_kwh_charged"], s["total_kwh_battery"]) == (delivered, 15.0)
    assert s["total_kwh_charged"] == db_reader.get_charge_stats()["total_kwh"]


def test_the_statistics_total_bills_a_fully_metered_merged_charge_on_its_meter(mate):
    """12 + 6 on the meter, 30 typed, re-tagged to Home: 18, the same as the Charges page."""
    pdb, _ = mate
    _charge(pdb, 1, 3, 10.0, ac=12.0, ctype="AC")
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type) VALUES"
        " (2,1,'2026-07-03T11:10:00+00:00','2026-07-03T12:00:00+00:00',70,80,5.0,6.0,'AC')")
    pdb._conn.commit()
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 30.0)
    db_reader.update_charge_type(1, "HOME")
    s = db_reader.get_stats_summary()
    assert (s["total_kwh_charged"], s["total_kwh_battery"]) == (18.0, 15.0)
    assert s["total_kwh_charged"] == db_reader.get_charge_stats()["total_kwh"]


def test_the_statistics_total_keeps_a_figure_typed_before_the_merge_as_that_pieces(mate):
    """12 typed on the first piece's card, then merged with a 5 kWh piece: 17, like the Charges
    page and like before the merge."""
    pdb, _ = mate
    _charge(pdb, 1, 3, 10.0, ctype="AC")
    db_reader.set_charge_gross_kwh(1, 12.0)
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type) VALUES"
        " (2,1,'2026-07-03T11:10:00+00:00','2026-07-03T12:00:00+00:00',70,80,5.0,'AC')")
    pdb._conn.commit()
    assert db_reader.merge_charges(1, 2)["ok"]
    s = db_reader.get_stats_summary()
    assert (s["total_kwh_charged"], s["total_kwh_battery"]) == (17.0, 15.0)
    assert s["total_kwh_charged"] == db_reader.get_charge_stats()["total_kwh"]


@pytest.mark.parametrize("lang, total, delivered, in_battery", [
    ("en", "113.5", "delivered", "105.1 kWh in battery"),
    ("pl", "113,5", "dostarczone", "105,1 kWh w baterii")])
def test_the_statistics_page_says_delivered_and_in_battery(mate, lang, total, delivered, in_battery):
    pdb, client = mate
    db_reader.set_setting("language", lang)
    _four_kinds(pdb)
    html = client.get("/statistics").text
    i = html.index({"en": "Energy Charged", "pl": "Naładowana energia"}[lang])
    tile = " ".join(html[i:html.index({"en": "Charge Sessions", "pl": "Sesje ładowania"}[lang], i)].split())
    assert f"{total}</span> <span class=\"text-slate-400 text-sm\">kWh</span>" in tile
    assert f">{delivered}</span> · <span title=" in tile and f"{in_battery}</span>" in tile
    assert "&lt;span" not in tile


def test_the_statistics_page_hides_the_battery_figure_when_it_would_repeat_the_total(mate):
    pdb, client = mate
    _charge(pdb, 3, 11, 20.0, ctype="AC")
    html = client.get("/statistics").text
    tile = html[html.index("Energy Charged"):html.index("Charge Sessions")]
    assert ">delivered</span>" in tile and "in battery" not in tile and " · " not in tile


def test_the_statistics_total_survives_a_database_the_poller_has_not_migrated(mate):
    """The web serves the poller's database and never alters it; between an update and the
    poller's next start the #222 column is absent, and a query that names it is a 500. The billed
    rule reaches Statistics through the same guard the Charges page has always had."""
    pdb, client = mate
    pdb._conn.execute("ALTER TABLE charges DROP COLUMN gross_kwh")
    pdb._conn.commit()
    _charge_no_gross(pdb, 1, 3, 27.5, ac=30.0)
    _charge_no_gross(pdb, 2, 9, 20.0, ctype="AC")
    assert db_reader._charges_have_gross(db_reader._get()) is False
    s = db_reader.get_stats_summary()
    assert (s["total_kwh_charged"], s["total_kwh_battery"]) == (50.0, 47.5)
    assert client.get("/statistics").status_code == 200


def _charge_no_gross(pdb, cid, day, batt, *, ac=None, ctype="HOME"):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type)"
        f" VALUES (?,1,'2026-07-{day:02d}T09:00:00+00:00','2026-07-{day:02d}T11:00:00+00:00',"
        "20,70,?,?,?)", (cid, batt, ac, ctype))
    pdb._conn.commit()
