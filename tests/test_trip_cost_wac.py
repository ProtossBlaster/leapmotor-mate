"""WAC (weighted-average-cost) trip pricing — GitHub #53. Pure-function tests for _wac_blend:
deterministic, no DB (the blend is capacity-free — it uses SoC ratios, so any consistent unit
for start/end_soc works).

Regola dal 31/07/26: il costo pagato si divide SEMPRE per l'energia arrivata IN BATTERIA
(energy_added_kwh), anche sulle ricariche di casa con wallbox. Prima si divideva per i kWh del
contatore, e cosi' il 10-15% dissipato dal caricatore — speso davvero — non finiva su nessun
viaggio. `rate` negli helper qui sotto e' quindi il prezzo pagato al MURO, e la miscela attesa e'
piu' alta di quello sulle ricariche di casa: e' esattamente la perdita di conversione."""
from db_reader import _wac_blend


def _home(ss, es, rate):
    dc = es - ss
    ac = dc / 0.9                      # HOME bills on AC (~10% more than the DC stored)
    return {"start_soc": ss, "end_soc": es, "cost": rate * ac,
            "ac_energy_kwh": ac, "location_type": "HOME", "energy_added_kwh": dc}


def _pub(ss, es, rate, kind="FAST"):
    dc = es - ss
    return {"start_soc": ss, "end_soc": es, "cost": rate * dc,
            "ac_energy_kwh": None, "location_type": kind, "energy_added_kwh": dc}


def test_no_charges_returns_none():
    assert _wac_blend([]) is None


def test_single_charge_is_its_own_rate():
    # Casa: paghi 0,25 al muro ma nel pacco arriva il 90% → l'energia nel pacco vale 0,25/0,9.
    assert abs(_wac_blend([_home(0, 100, 0.25)]) - 0.25 / 0.9) < 1e-9
    # Colonnina: nessun contatore, muro e batteria coincidono → la tariffa non si muove.
    assert abs(_wac_blend([_pub(20, 80, 0.55)]) - 0.55) < 1e-9


def test_home_then_hpc_blends_by_energy():
    # The issue's example: 40 kWh @0.25 + 20 kWh @0.75 over 60 kWh -> 0.4167 €/kWh.
    charges = [_home(0, 100, 0.25), _pub(61.5384615, 92.3076923, 0.75, "HPC")]
    # 40 kWh di casa a 0,25/0,9 + 20 kWh a 0,75, su 60 kWh.
    exp = (40 * (0.25 / 0.9) + 20 * 0.75) / 60
    assert abs(_wac_blend(charges) - exp) < 1e-3


def test_consumption_between_charges_does_not_change_the_blend():
    # Two charges at the SAME rate, whatever the SoC drop between them -> blend stays that rate.
    assert abs(_wac_blend([_home(0, 100, 0.25), _home(40, 90, 0.25)]) - 0.25 / 0.9) < 1e-9


def test_blend_is_bounded_by_the_prices_actually_paid():
    charges = [_home(0, 100, 0.20), _pub(40, 70, 0.35, "AC"),
               _pub(35, 85, 0.55, "FAST"), _pub(40, 90, 0.79, "HPC")]
    p = _wac_blend(charges)
    assert 0.20 <= p <= 0.79          # always a convex mix of paid prices


def test_unconfirmed_charge_is_carry_forward():
    # A charge with cost=None (unconfirmed, non-HOME) must NOT move the blend.
    unconf = {"start_soc": 60, "end_soc": 90, "cost": None, "ac_energy_kwh": None,
              "location_type": None, "energy_added_kwh": 30}
    base = [_home(0, 100, 0.25)]
    assert _wac_blend(base) == _wac_blend(base + [unconf])


def test_capacity_free_uses_soc_ratios():
    # Halving every SoC span (as a capacity change would) leaves the blend unchanged.
    a = [_home(0, 100, 0.25), _pub(60, 90, 0.75)]
    b = [_home(0, 50, 0.25),  _pub(30, 45, 0.75)]
    assert abs(_wac_blend(a) - _wac_blend(b)) < 1e-9


def test_zero_or_negative_rise_charges_are_ignored():
    # A "charge" with no SoC rise can't anchor the mix -> ignored, blend unchanged.
    weird = _pub(80, 80, 0.99)        # end == start
    assert _wac_blend([_home(0, 100, 0.25), weird]) == _wac_blend([_home(0, 100, 0.25)])
