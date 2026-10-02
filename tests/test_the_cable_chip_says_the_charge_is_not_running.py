"""The cable chip over the car carries the charge, the state word the car: with the cable in and nothing
flowing the chip says "Cable connected · 87% (Not charging)", or "(Charge complete)" once the charge
finished, and the word under it says "Parked", as does the State of the status card. The two no longer
repeat each other, and a car on a charger that withholds the current reads as what it is: parked, with
the cable in.
"""
import re

import pytest

pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

from test_the_cable_shows_while_the_charger_holds import car  # noqa: F401


def _chip(hero):
    chips = [s for s in re.findall(r'<span class="hero-chip.*?</span>', hero, re.DOTALL) if "Cable connected" in s]
    assert len(chips) == 1, f"the cable on {len(chips)} chips"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", chips[0])).strip()


def _word(hero):
    return re.search(r'<div class="text-center text-sm font-semibold[^"]*">([^<]*)</div>', hero).group(1)


def _state(card):
    return re.search(r'<div class="stat-label">State</div>\s*<div[^>]*>([^<]*)<', card, re.DOTALL).group(1).strip()


def test_a_held_cable_says_not_charging_and_the_car_parked(car):
    car.frame(plug_connected=0, ac_port_mode=1)
    hero = car.get("/api/overview-hero")
    assert _chip(hero) == "Cable connected · 87% (Not charging)"
    assert _word(hero) == "Parked"
    assert hero.count("Cable connected") == 1
    assert _state(car.get("/api/status-card")) == "Parked"


def test_a_completed_charge_says_so_on_the_chip(car):
    car.frame(plug_connected=1, charge_completed=1, soc=100)
    hero = car.get("/api/overview-hero")
    assert _chip(hero) == "Cable connected · 100% (Charge complete)"
    assert _word(hero) == "Parked"


def test_the_chip_speaks_the_readers_language(car):
    db = __import__("db_reader")
    db.set_setting("language", "pl")
    car.frame(plug_connected=0, ac_port_mode=1)
    hero = car.get("/api/overview-hero")
    assert "Kabel podłączony · 87% (Nie ładuje)" in re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", hero))
    assert _word(hero) == "Zaparkowany"
