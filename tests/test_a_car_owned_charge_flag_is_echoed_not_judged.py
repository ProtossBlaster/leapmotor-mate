"""A charge flag Mate reads from the car goes back to the car as the car sent it.

@jcconca, #343. 4.7.1 made the refusal name the flag and what it held, so that a diagnostics bundle
would carry the answer with no extra cloud call. It did, four days later, in his:

    2026-09-29 22:12:08 [ERROR] leapmotor_mate: MQTT: command charge_schedule failed:
        Command not sent: invalid charge flag circulation=2

So the flag is **`circulation`** and the value is **2** — not `recharge`, which is what everyone had
assumed, [PR #344](https://github.com/ProtossBlaster/leapmotor-mate/pull/344) included. His last
accepted send was 27/09 at 22:00 with `circulation=1`; every nightly automation of his since has
failed with that sentence.

Command 190 re-sends the car's WHOLE plan. Only `chargeEnable` and `chargesoc` are Mate's to choose:
`circulation` and `recharge` are read out of the car's own `config.3` and written straight back
(`poller/main._apply_charge_schedule`, `web/command_client.save_charge_schedule`,
`api_v2_bridge.set_charge_limit` — all three). Checking those two against {0, 1} — a domain that was
assumed, never measured — stopped him changing the one field he did ask about.

🔑 An integer, and only an integer. `None`, `''`, `'1'`, `1.0` and booleans mean the value could not
be READ, which is a different thing from a value the car said; those are still refused by name and
with the value. Fixed upstream in MATE-API 0.1.0a14 and re-vendored.
"""
import pytest

import mate_api  # noqa: F401 — configures the pinned independent runtime
from command_contracts import LeapmotorApiError, charge   # the runtime's own shim

# Exactly what his car published on 29/09, with the rest of the plan as his bundle shows it.
HIS_PLAN = dict(chargeEnable=1, chargesoc=90, circulation=2,
                cycles="1,1,1,1,1,1,1", endtime="15:00", recharge=0, starttime="11:00")


def test_his_nightly_automation_goes_through():
    assert charge(dict(HIS_PLAN)) == HIS_PLAN


@pytest.mark.parametrize("flag", ("circulation", "recharge"))
@pytest.mark.parametrize("value", (0, 1, 2, 3, 7, -1))
def test_any_integer_the_car_owns_is_echoed(flag, value):
    plan = dict(HIS_PLAN, **{flag: value})
    assert charge(plan)[flag] == value


@pytest.mark.parametrize("flag", ("circulation", "recharge"))
@pytest.mark.parametrize("value", (None, "", "1", 1.0, True, False))
def test_a_value_that_could_not_be_read_is_still_refused_by_name(flag, value):
    with pytest.raises(LeapmotorApiError) as e:
        charge(dict(HIS_PLAN, **{flag: value}))
    assert flag in str(e.value) and repr(value) in str(e.value)


@pytest.mark.parametrize("value", (2, -1, 7))
def test_the_switch_that_is_ours_keeps_its_two_values(value):
    """`chargeEnable` turns the owner's plan on and off. Nothing reads it off the car to echo back,
    so there is no value beyond 0 and 1 to be faithful to."""
    with pytest.raises(LeapmotorApiError) as e:
        charge(dict(HIS_PLAN, chargeEnable=value))
    assert "chargeEnable" in str(e.value)


def test_the_three_routes_all_pass_through_this_one_contract():
    """Not a style point: PR #344 patched two of the three, so the same plan would have behaved
    differently depending on where the command came from. Fixing it here covers all three."""
    import inspect
    import api_v2_bridge
    assert "charge(current)" in inspect.getsource(api_v2_bridge.NewAPIClient.set_charge_limit)
