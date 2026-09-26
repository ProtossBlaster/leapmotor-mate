"""A car's known telemetry faults live in one table keyed by the car, and the frame is corrected
once, right after it is parsed, so that every reader sees a reading the car can vouch for.

The first row: a C10 range extender charging on AC reads ~0 A on the pack current through the whole
charge, because the on-board charger feeds the pack past that sensor (v2.8.4 opens the session on
the cable state and the remaining time for that reason). Forwarded as a measurement — to ABRP as the
car's power, to Home Assistant as Charge Current and Charge Power, to the recorder as the session's
peak — that is a false zero on every point, so a car with the flag has no pack current and no
charge power while plugged into AC, and everything else as before. Plugged,
not charging: on that car the cable state flickers 2 → 1 → 3 → 2 mid-charge and charge detection
follows it, while the on-board charger keeps feeding the pack.
"""
import pathlib
import types

import abrp
import client
import pytest
import quirks


def _car(car_type):
    return types.SimpleNamespace(car_type=car_type)


def _frame(sig):
    return client._parse_signal("VIN", sig)


_C10_ON_AC = {"1010": 0, "1319": 0, "1149": 2, "1200": 340, "1178": 0.1, "1177": 402.0, "3235": 40}
_C10_BEV_ON_AC = {k: v for k, v in _C10_ON_AC.items() if k != "3235"}


def test_the_table_knows_the_c10_range_extender_and_nobody_else():
    assert quirks.PACK_CURRENT_BLIND_TO_AC_CHARGE in quirks.for_car(_car("C10"), _frame(_C10_ON_AC))
    assert quirks.for_car(_car("c10"), _frame(_C10_ON_AC)) == quirks.for_car(_car("C10"), _frame(_C10_ON_AC))
    assert quirks.for_car(_car("C10"), _frame(_C10_BEV_ON_AC)) == frozenset()
    for car_type in ("B10", "T03", "B05", "", None):
        for sig in (_C10_ON_AC, _C10_BEV_ON_AC):
            assert quirks.for_car(_car(car_type), _frame(sig)) == frozenset(), car_type
    assert quirks.for_car(types.SimpleNamespace(), _frame(_C10_ON_AC)) == frozenset()   # no car_type at all


def _fixed(sig, car=_car("C10")):
    return quirks.fix_frame(_frame(sig), car)


def test_a_flagged_car_plugged_into_ac_has_no_pack_current_and_no_power():
    vd = _fixed(_C10_ON_AC)
    assert vd.charge_current_a is None and vd.charge_power_kw is None
    assert vd.charging_status == 1 and vd.charge_voltage_v == 402.0   # the rest of the frame stands


def test_the_cable_flicker_mid_charge_does_not_let_a_false_zero_through():
    """1149 goes 2 → 1 → 3 → 2 on this car while the SoC keeps rising; charge detection follows the
    cable, the fixup follows the plug."""
    for state, soc in ((2, 40.0), (1, 40.2), (3, 40.4), (2, 40.6)):
        vd = _fixed({**_C10_ON_AC, "1149": state, "1204": soc})
        assert vd.charge_current_a is None and vd.charge_power_kw is None, state


def test_a_flagged_car_waiting_for_its_charge_window_has_none_either():
    vd = _fixed({**_C10_ON_AC, "1149": 4, "1200": 0})
    assert vd.charging_status == 0 and vd.charge_current_a is None


def test_a_flagged_car_on_the_dc_gun_keeps_its_current():
    vd = _fixed({**_C10_ON_AC, "1197": 1, "1178": -120.0})
    assert vd.charge_current_a == -120.0 and vd.charge_power_kw == 48.24


def test_a_flagged_car_powering_a_load_over_v2l_keeps_its_current():
    """V2L discharges through the pack, so 1178 is real there; the AC port reads 2 and the cable
    state may well read connected."""
    vd = _fixed({**_C10_ON_AC, "47": 2, "1149": 1, "1200": 0, "1178": 8.5})
    assert vd.v2l_active and vd.charge_current_a == 8.5


def test_a_flagged_car_driving_keeps_its_current():
    vd = _fixed({**_C10_ON_AC, "1010": 3, "1319": 50.0, "1149": 5, "1178": 60.0})
    assert vd.charge_current_a == 60.0


def test_a_flagged_car_resting_off_the_cable_keeps_its_zero():
    assert _fixed({**_C10_ON_AC, "1149": 0, "1200": 0, "1178": 0.0}).charge_current_a == 0.0


def test_a_car_without_the_flag_keeps_the_frame_as_parsed():
    vd = _frame(_C10_ON_AC)
    assert quirks.fix_frame(vd, _car("B10")) is vd
    bev = _frame(_C10_BEV_ON_AC)
    assert quirks.fix_frame(bev, _car("C10")) is bev


def test_abrp_then_gets_the_charge_without_a_false_zero_power():
    tlm = abrp._build_tlm(_fixed(_C10_ON_AC))
    assert tlm["is_charging"] is True and tlm["voltage"] == 402.0
    assert "power" not in tlm and "current" not in tlm


def _poller_main():
    """poller/main.py under its own name — a bare `import main` gets web/main.py."""
    import importlib.util
    import sys
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_quirks", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["poller_main_quirks"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("car_type, current_seen, power_seen", [("C10", None, None), ("B10", 0.1, 0.04)])
def test_the_poll_loop_fixes_the_frame_before_anyone_reads_it(tmp_path, monkeypatch, car_type, current_seen, power_seen):
    """One real poll: the cloud answers with the C10-on-AC frame, and what the recorder — the first
    reader — receives has no pack current on the flagged car and the measured one on another."""
    import db as D
    PM = _poller_main()
    vin = f"VINQUIRK{car_type}000001"
    db = D.Database(str(tmp_path / "quirk.db"))
    vid = db.ensure_vehicle(vin, car_type)

    class _Vehicle:
        pass
    _Vehicle.vin, _Vehicle.car_type, _Vehicle.year, _Vehicle.abilities, _Vehicle.is_shared = vin, car_type, 2025, None, False

    class _Client:
        def get_status(self, vehicle=None):
            return client._parse_signal(vin, _C10_ON_AC)

    seen = []
    process = PM.Recorder.process
    monkeypatch.setattr(PM.Recorder, "process", lambda self, data: (seen.append(data), process(self, data)))
    ctx = PM.VehicleContext(db, _Vehicle(), vid)
    PM._poll_vehicle(db, _Client(), ctx, PM.AccountState())
    assert len(seen) == 1
    assert seen[0].charge_current_a == current_seen and seen[0].charge_power_kw == power_seen
    assert seen[0].charging_status == 1
