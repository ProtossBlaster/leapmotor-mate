"""With no GPS fix the outside-temperature sampler asked Open-Meteo for 0,0 (30/08 audit).

`OutsideTempSampler.sample` guards with `if lat is None or lon is None` — and nothing upstream ever
sends None. `client._resolve_coord` says so in its own docstring: *"Returns 0.0 when no usable value
exists"*. So the guard is dead code, and a frame with no fix asked for the weather at latitude 0,
longitude 0 — Null Island, in the Gulf of Guinea — then stored that as the car's outside temperature
and moved the sampler's anchor there, which also suppresses the next real fetch until the car has
"moved" 10 km back.

The rest of the poller already knows the convention: `poller/db.py` writes geohashes only
`if data.latitude and data.longitude`, and its trip query filters `latitude != 0 AND longitude != 0`.
The sampler is now the same: a fix at exactly 0,0 is no fix.

CI-safe: pure sampler logic, no network, no db.
"""
import outside_temp


class _Spy:
    """Stands in for the Open-Meteo call and remembers where it was asked about."""
    def __init__(self, answer=21.5):
        self.calls, self.answer = [], answer

    def __call__(self, lat, lon):
        self.calls.append((lat, lon))
        return self.answer


def test_no_fix_never_asks_for_the_gulf_of_guinea():
    spy = _Spy()
    s = outside_temp.OutsideTempSampler(fetch=spy)

    assert s.sample(0.0, 0.0, 1_000.0) is None
    assert spy.calls == [], f"asked the weather at {spy.calls}"


def test_no_fix_keeps_the_last_real_reading():
    """A frame without a fix must not erase what the last real one measured — the car did not
    teleport, it just went quiet."""
    spy = _Spy(19.0)
    s = outside_temp.OutsideTempSampler(fetch=spy)
    assert s.sample(45.4, 9.2, 1_000.0) == 19.0          # a real fix in Milan

    spy.answer = -99.0                                    # would be obvious if it were used
    assert s.sample(0.0, 0.0, 9_000.0) == 19.0            # no fix → keep Milan's reading
    assert spy.calls == [(45.4, 9.2)], f"extra calls: {spy.calls}"


def test_a_real_fix_still_fetches():
    """The guard must not silence the feature: a genuine position is still looked up."""
    spy = _Spy(4.0)
    s = outside_temp.OutsideTempSampler(fetch=spy)
    assert s.sample(45.4, 9.2, 1_000.0) == 4.0
    assert spy.calls == [(45.4, 9.2)]
