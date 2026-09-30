"""A model's default pack must be a pack Mate actually offers for that model — and the A10/B03X
must have one at all.

Two tables decide a car's kWh, and they are written in two different files:

  * `db.BATTERY_CAPACITY_DEFAULTS` — what a car gets when nobody chose (a second car appearing
    through `ensure_vehicle`, an install that never reached the wizard's battery step);
  * `battery_packs.EU_BATTERY_MAP` — what the wizard offers when the cloud's model is recognised.

Nothing held them together. A default that is not in the map is a pack no owner can pick and no
page ever explains, applied in silence to every kWh, €/kWh and consumption figure that install
prints. The first test is the seam.

🔑 The A10 is why this file exists. @dommi1966's car (#338) reports `A10` from the cloud — the
CHINESE project name of the car sold in Europe as the **B03X**; Leapmotor renamed it because "A10"
read as an A-segment while the car is a B (the same convention by which the B10 is a C and the C10
a D). Neither table had a row for it, so:

  * `default_capacity_for("A10")` returned the unknown-model fallback of **65.0 kWh** — a pack this
    car has never been built with, 25% above even its larger one;
  * the wizard offered him no pack at all, so he typed one by hand: **53.0**, which is the
    NAMEPLATE (gross) figure, not the usable one. Right car, wrong side of the buffer.

⚠️ The usable figures, not the nameplate — the mistake this repo has already made once. The hint
under the manual field used to quote `B10 Pro: 56.2 · T03: 37.3` (gross) on the same line as
`C10 RWD: 67.0` (net), and the C10 RWD sat at 69.9 until real charges showed the battery taking
100.8% of what the charger delivered (#246). So for the B03X, read from two sources that print both
columns:

    39.8 gross → 39.0 usable (0.8 kWh / 2.0% buffer)
    53.0 gross → 52.0 usable (1.0 kWh / 1.9% buffer)

EV Database and EVKX agree on both variants, and the two buffers agree with each other — which is
the cross-check the C10 failed.
"""
import pytest

import battery_packs
import db as D


DEFAULTS = D.BATTERY_CAPACITY_DEFAULTS


# ── the seam between the two tables ───────────────────────────────────────────
@pytest.mark.parametrize("car_type", sorted(DEFAULTS))
def test_every_model_default_is_a_pack_the_wizard_offers(car_type):
    """A default outside the map is a pack nobody can choose and nobody can check."""
    offered = {float(o["v"]) for o in battery_packs.EU_BATTERY_MAP.get(car_type, [])}
    assert offered, f"{car_type} has a default of {DEFAULTS[car_type]} but the wizard offers nothing"
    assert DEFAULTS[car_type] in offered, (
        f"{car_type} defaults to {DEFAULTS[car_type]}, which is not among {sorted(offered)}")


# ── the A10 itself ────────────────────────────────────────────────────────────
def test_a_b03x_is_not_given_the_unknown_model_fallback():
    """65.0 is the fallback for a car Mate has never heard of. The B03X is not one of those, and it
    has never been built with a 65 kWh pack."""
    assert D.default_capacity_for("A10") != D.BATTERY_CAPACITY_FALLBACK
    assert D.default_capacity_for("A10") == 52.0
    assert D.default_capacity_for("a10") == 52.0, "the cloud's case must not decide the pack"


def test_the_a10_packs_are_the_usable_figures_not_the_nameplate():
    """39.0/52.0 (usable), never 39.8/53.0 (gross). Both variants are offered: the wizard has to
    ask, because the cloud sends the model and never the pack."""
    offered = {o["v"] for o in battery_packs.EU_BATTERY_MAP["A10"]}
    assert offered == {"39.0", "52.0"}
    assert not offered & {"39.8", "53.0"}, "a nameplate figure reached the usable-capacity field"


def test_the_a10_packs_are_never_labelled_b03():
    """B03 and B03X are two different cars: the crossover (A10, 4270 mm) and the hatchback
    (A05, 4175 mm, due after it). They are one character apart in writing, so the label is the only
    thing standing between them in the wizard — and a B03 owner who picks a pack off the A10's row
    would be choosing a figure Leapmotor has never published for their car.

    `import re` locally: the guard is the negative lookahead, and it is the whole test."""
    import re
    for o in battery_packs.EU_BATTERY_MAP["A10"]:
        assert "B03X" in o["label"], f"{o['v']} no longer names the car it belongs to"
        assert not re.search(r"B03(?!X)", o["label"]), \
            f"{o['v']} is labelled for the B03, which is the A05 — a different car"


def test_no_row_claims_the_b03_under_either_name():
    """The B03 is the A05 — a DIFFERENT car from the B03X, and one nobody can buy yet: Italy at the
    end of 2026, the UK in 2027. Its specification is not published either (no usable figure in
    either source, no European WLTP, trims unannounced), so a row written from the B03X's numbers
    would be an unconfirmed capacity in the field that decides every kWh that install prints.

    The two cars share the platform, the wheelbase and — per the press — the packs, which is exactly
    what makes copying the row tempting. `default_capacity_for` answering the fallback for an A05 is
    the CORRECT behaviour today: it is a car we have never seen, and the log says so by name."""
    assert "A05" not in battery_packs.EU_BATTERY_MAP
    assert "B03" not in battery_packs.EU_BATTERY_MAP
    assert "A05" not in D.BATTERY_CAPACITY_DEFAULTS
    assert "B03" not in D.BATTERY_CAPACITY_DEFAULTS


def test_the_a10_is_not_a_range_extender():
    """No REEV variant exists — the flag would hide the packs from the official build (#141)."""
    assert not any(o.get("reev") for o in battery_packs.EU_BATTERY_MAP["A10"])


# ── what the owner actually meets ─────────────────────────────────────────────
def test_a_b03x_owner_is_asked_which_pack_instead_of_typing_one(monkeypatch):
    """The table is only half the fix: the wizard has to ASK. Before the A10 row existed the
    endpoint fell through to the manual field, which is how a gross figure got typed into a usable
    one. Guarded in-function rather than at module scope so the tables above stay protected in the
    minimal CI env, which has no fastapi."""
    pytest.importorskip("fastapi", reason="the endpoint needs fastapi")
    import asyncio
    import json

    import main
    import research

    class _JsonReq:
        headers: dict = {}

        async def json(self):
            return {"user": "u@example.com", "password": "pw", "pin": "1234"}

    monkeypatch.setattr(research, "research_enabled", lambda: False)
    monkeypatch.setattr(main.command_client, "detect_vehicle",
                        lambda u, p, pin: {"car_type": "A10", "vin": "LVIN0000000000001"})
    body = json.loads(asyncio.run(main.detect_vehicle_api(_JsonReq())).body)

    assert {o["v"] for o in body.get("battery_options", [])} == {"39.0", "52.0"}
    assert "battery_kwh" not in body, "two variants exist — nothing may be auto-set"
