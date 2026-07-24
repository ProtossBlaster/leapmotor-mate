"""REEV-only: a home AC charge is detected from the battery climbing (beta #12, michapr's B10).

His diagnostics proved the normal detector can never fire on that car: across 15 days the poller
entered CHARGING exactly zero times while the SoC visibly climbed (36.4 → 36.8 % in three
minutes, state stuck on "parked"). The cable reports 1 ("connected"), never 2 ("charging"), and
the pack current reads ~0.1 A instead of a charge current — so neither half of the usual test is
ever true.

The rise itself is therefore the evidence, but ONLY on a REEV. Pure EVs report cable and current
correctly and work today; they must not be touched, so every test here has a BEV twin proving
the new path is unreachable for them.
"""
import types

from state_machine import StateMachine, State


def _data(soc, *, is_reev, plugged=True, gear="P", speed=0.0, charging_status=0):
    """Minimal VehicleData stand-in — the state machine only reads these fields."""
    return types.SimpleNamespace(
        soc=soc, is_reev=is_reev, plug_connected=plugged, gear=gear, speed_kmh=speed,
        charging_status=charging_status, ac_port_mode=0,
        fingerprint=lambda: (soc, gear, speed, plugged),
    )


def _run(sm, socs, **kw):
    """Feed a SoC sequence; return the states entered."""
    seen = []
    for s in socs:
        for ev in sm.update(_data(s, **kw)):
            seen.append(ev.to_state)
    return seen


# ── the real case ────────────────────────────────────────────────────────────

def test_reev_charge_detected_from_soc_rise():
    """michapr's actual numbers: cable connected, current ~0, battery climbing."""
    sm = StateMachine()
    states = _run(sm, [36.4, 36.5, 36.7, 36.9, 37.1], is_reev=True)
    assert State.CHARGING in states


def test_a_pure_ev_is_never_touched_by_this():
    """Same climb, same cable — but a BEV must NOT take the new path (it has working signals)."""
    sm = StateMachine()
    states = _run(sm, [36.4, 36.5, 36.7, 36.9, 37.1], is_reev=False)
    assert State.CHARGING not in states


# ── the false positives it must avoid ────────────────────────────────────────

def test_scheduled_charge_waiting_does_not_open_a_session():
    """Cable in, waiting for its slot: the battery is FLAT. This is exactly the case the old
    comment warns must never start counting a charge."""
    sm = StateMachine()
    states = _run(sm, [42.0, 42.0, 42.0, 42.0], is_reev=True)
    assert State.CHARGING not in states


def test_soc_noise_does_not_open_a_session():
    """A 0.1 % wobble is the SoC's own read step, not a charge."""
    sm = StateMachine()
    states = _run(sm, [42.0, 42.1, 42.0, 42.1], is_reev=True)
    assert State.CHARGING not in states


def test_unplugged_rise_does_not_open_a_session():
    """No cable, no charge — whatever the battery reports."""
    sm = StateMachine()
    states = _run(sm, [36.4, 36.8, 37.2], is_reev=True, plugged=False)
    assert State.CHARGING not in states


def test_driving_never_opens_a_charge():
    """A REEV driving on its generator gains SoC while moving — must stay a trip, not a charge."""
    sm = StateMachine()
    states = _run(sm, [36.4, 36.9, 37.4], is_reev=True, gear="D", speed=50.0)
    assert State.CHARGING not in states


def test_reference_resets_when_the_cable_comes_out():
    """Unplug, drive, plug back in later at a higher SoC: the stale reference must not be
    mistaken for a rise the moment the cable returns."""
    sm = StateMachine()
    _run(sm, [30.0, 30.1], is_reev=True)                       # plugged, tiny wobble
    _run(sm, [30.1, 45.0], is_reev=True, plugged=False)        # unplugged, big jump
    states = _run(sm, [45.0], is_reev=True)                    # plugged again, first sample
    assert State.CHARGING not in states                        # no rise measured yet
