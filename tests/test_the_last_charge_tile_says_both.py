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
import pathlib

import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


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
