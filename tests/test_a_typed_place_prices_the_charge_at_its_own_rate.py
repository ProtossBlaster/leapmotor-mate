"""A place that declares its own type must also be PAID for as that type.

#351 gave a place its own `charge_type` and proved the type is written. The type alone is not the
feature: an owner types a workplace AC point so its charges are counted and **priced** as public
AC, and a free municipal charger so they cost nothing. Those two live in different branches of
`compute_cost` — the place's rate is read for every type, while FREE returns 0 before the rate is
looked at at all — so a change that got the type right could still have got the money wrong.

Written while reviewing #351 (30/09/2026), because its own tests stop at `location_type`.
"""
from test_charging_places import store, place, closed, row  # noqa: F401 — pytest fixtures
import db_reader as W


def _typed(store, kind, rate):
    pid = place(store, name=f'Place {kind}', rate=rate)
    store._conn.execute('UPDATE charging_places SET charge_type=? WHERE id=?', (kind, pid))
    store._conn.commit()
    return pid


def test_an_ac_place_prices_the_charge_at_its_own_rate(store):
    """`closed()` leaves 10 kWh in the battery; 10 x 0,45 is what the owner pays."""
    cid = closed(store)
    W.assign_charging_place(cid, _typed(store, 'AC', .45))
    r = row(store, cid)
    assert r['location_type'] == 'AC'
    assert r['cost'] == 4.5


def test_an_hpc_place_prices_the_charge_at_its_own_rate(store):
    """The type is asserted here too, on purpose: the place's rate is read for HOME as well, so a
    charge wrongly left on HOME would come out at the same 7,90 and the price alone proves nothing.
    Found by mutation — this test passed against the defect until the type was asserted."""
    cid = closed(store)
    W.assign_charging_place(cid, _typed(store, 'HPC', .79))
    r = row(store, cid)
    assert r['location_type'] == 'HPC'
    assert r['cost'] == 7.9


def test_a_free_place_costs_nothing_whatever_rate_it_carries(store):
    """A rate left on a place later marked FREE must not come back as a price."""
    cid = closed(store)
    W.assign_charging_place(cid, _typed(store, 'FREE', .45))
    r = row(store, cid)
    assert r['location_type'] == 'FREE'
    assert r['cost'] == 0.0


def test_a_home_place_is_priced_exactly_as_before(store):
    cid = closed(store)
    W.assign_charging_place(cid, _typed(store, 'HOME', .30))
    r = row(store, cid)
    assert r['location_type'] == 'HOME'
    assert r['cost'] == 3.0
