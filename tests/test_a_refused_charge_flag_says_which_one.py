"""When Mate refuses to send a charge schedule, the refusal names the flag and its value.

@jcconca, #343: saving a schedule answered `Command not sent: invalid charge flag` and nothing
else. Three flags travel in that command — `chargeEnable`, `circulation`, `recharge` — and any
value of any of them that is not the integer 0 or 1 produced that same sentence. Run against the
real contract, all twenty-four combinations of {flag} x {2, -1, None, True, False, "1", 1.0, ""}
gave one indistinguishable message.

Two of the three are not Mate's to choose: `circulation` and `recharge` are read from the car and
written straight back (`_apply_charge_schedule`, `save_charge_schedule`), so when the car publishes
something else the owner is stopped by a sentence that cannot be acted on — and neither can we. The
issue's first comment had to ask the reporter to read those fields out of his own car.

Naming them costs nothing and the poller already logs the refusal, so from here the diagnostics
bundle carries the answer with no extra cloud call and no new setting.

⚠️ The values here are flags — 0, 1, whatever the car said. No VIN, no token, nothing to redact.

🔑 And it worked: four days later his next bundle carried `invalid charge flag circulation=2`, on a
real C10 — which is why an INTEGER the car published is no longer a fault for the two flags Mate
only echoes. → tests/test_a_car_owned_charge_flag_is_echoed_not_judged.py
"""
import pytest

import mate_api  # noqa: F401 — configures the pinned independent runtime
from command_contracts import LeapmotorApiError, charge  # the runtime's own shim

BASE = dict(chargeEnable=1, chargesoc=90, circulation=1,
            cycles="1,1,1,1,1,1,1", endtime="15:00", recharge=0, starttime="11:00")

# What Mate could not READ. An integer the CAR published is a different thing, and for the two
# flags Mate reads from the car and writes straight back it now goes through unchanged.
UNREADABLE = (None, True, False, "1", 1.0, "")
# `chargeEnable` is Mate's own switch: nothing reads it off the car, so 0/1 is all it can be.
REFUSED_FOR = {"chargeEnable": (2, -1) + UNREADABLE,
               "circulation": UNREADABLE, "recharge": UNREADABLE}


def _refusal(**over):
    state = dict(BASE, **over)
    with pytest.raises(LeapmotorApiError) as e:
        charge(state)
    return str(e.value)


def test_the_car_s_own_schedule_still_goes_through():
    """The premise: exactly what Mate sent and the cloud accepted on @jcconca's C10, 22-27 Sep."""
    assert charge(dict(BASE)) == BASE


@pytest.mark.parametrize("flag,value", [(f, v) for f, vs in REFUSED_FOR.items() for v in vs])
def test_the_refusal_names_the_flag_and_what_it_held(flag, value):
    message = _refusal(**{flag: value})
    assert flag in message, f"{flag}={value!r} refused without saying which flag: {message!r}"
    assert repr(value) in message or str(value) in message, \
        f"{flag}={value!r} refused without saying what it held: {message!r}"


def test_two_different_faults_do_not_read_the_same():
    """The defect itself: one sentence over three flags. Whatever the wording, `circulation` going
    wrong must not print what `recharge` going wrong prints."""
    assert _refusal(circulation=None) != _refusal(recharge=None)
    assert _refusal(circulation=None) != _refusal(circulation="")


def test_a_day_mask_with_no_days_still_says_that_instead():
    """The neighbouring refusal keeps its own words — an enabled window with every day off is a
    different fault and was never the one being reported."""
    assert "day" in _refusal(cycles="0,0,0,0,0,0,0")
