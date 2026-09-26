"""A charge power the car cannot vouch for is None, and no reader mistakes it for a figure.

`charge_power_kw` is |current × voltage| from signals 1178 and 1177. The parser always computes
it, but a frame whose current is not the charge (a car whose on-board charger feeds the pack past
the sensor) has no honest power either. The value may therefore be None, and everything that reads
it — the session's peak, the car-side energy behind a stuck wallbox counter, regen, the MQTT
sensor — treats None as no reading rather than as 0 kW.
"""
import recorder as R
from client import VehicleData
from state_machine import State


def _frame(**kw):
    base = dict(
        vin="TESTVIN", timestamp_ms=1_000, soc=60.0, range_km=200, odometer_km=1000.0,
        speed_kmh=0.0, gear="P", vehicle_state="parked", charging_status=1, charge_power_kw=None,
        latitude=51.8, longitude=5.8, outside_temp=None, inside_temp=20.0, climate_target_temp=21.0,
        battery_min_temp=15.0, is_locked=True, climate_on=False, climate_cooling=False,
        climate_heating=False, climate_defrost=False, trunk_open=False, windows_open=False,
        sunshade_open=False, any_door_open=False, plug_connected=True, remaining_charge_min=120,
        charge_voltage_v=402.0, charge_current_a=None)
    base.update(kw)
    return VehicleData(**base)


class _SpyDB:
    """What a mid-charge recorder touches; the peak writes are what the test watches."""
    def __init__(self):
        self.peaks = []
    def save_position(self, vid, data): pass
    def get_open_charge(self, vid): return None
    def update_charge_max_power(self, charge_id, kw): self.peaks.append(kw)
    def add_trip_position(self, trip_id, data): pass
    def note_wallbox_unread(self, *a, **kw): pass


def _charging_recorder(db):
    rec = R.Recorder(db, vehicle_id=1)
    rec._started = True
    rec._sm.state = State.CHARGING
    rec._active_charge_id = 3
    rec._last_soc, rec._last_odometer = 60.0, 1000.0
    return rec


def test_an_unknown_power_does_not_become_the_sessions_peak():
    db = _SpyDB()
    rec = _charging_recorder(db)
    rec.process(_frame(charge_power_kw=None))
    assert rec._max_charge_kw == 0.0 and db.peaks == []


def test_a_measured_power_still_does():
    db = _SpyDB()
    rec = _charging_recorder(db)
    rec.process(_frame(charge_power_kw=7.2, charge_current_a=-18.0))
    assert rec._max_charge_kw == 7.2 and db.peaks == [7.2]


def test_an_unknown_power_counts_nothing_against_a_stuck_wallbox_counter(monkeypatch):
    db = _SpyDB()
    seen = []
    db.accumulate_wallbox_energy = lambda cid, wb, car_kwh: seen.append(car_kwh)
    rec = _charging_recorder(db)
    rec._charge_at_wallbox = True
    monkeypatch.setattr(rec, "_read_wallbox_energy", lambda: 12.5)
    rec.process(_frame(charge_power_kw=None))
    assert seen == [0.0]


def test_regen_with_an_unknown_power_adds_nothing():
    rec = R.Recorder(_SpyDB(), vehicle_id=1)
    rec._started = True
    rec._sm.state = State.DRIVING
    rec._active_trip_id = 7
    rec._last_soc, rec._last_odometer = 80.0, 1000.0
    rec.process(_frame(charging_status=0, plug_connected=False, vehicle_state="driving", gear="D",
                       speed_kmh=50.0, charge_current_a=-20.0, charge_power_kw=None))
    assert rec._regen_kwh == 0.0
