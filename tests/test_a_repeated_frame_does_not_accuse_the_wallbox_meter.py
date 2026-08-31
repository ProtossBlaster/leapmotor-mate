"""A frame the cloud is only RE-SERVING must not count as energy the car drew.

THE BUG. While charging at a mapped wallbox the recorder sends `accumulate_wallbox_energy`
what the CAR says it took over this poll (`charge_power_kw × poll_interval`). That figure
exists for one purpose (#215): to tell a STOPPED meter from a slow one — while the counter
does not move, the car-reported energy piles up in `wb_stuck_kwh`, and past 3.0 kWh
`finalize_charge` decides the meter is dead and throws the whole measured AC total away,
billing the charge on the battery instead.

But an unreachable car does not make the cloud say so: it re-serves the LAST frame,
identical payload, identical timestamp, poll after poll — the same lie `stale` already
guards against for saved positions and for regen, twenty lines above. That frozen frame
still reads "7 kW", so a car that stopped charging (or fell off the network entirely) keeps
"drawing" 7 kW on paper while the wallbox counter, correctly, stands still.

MEASURED: 7 kW at the default 30 s parked cadence is 0.0583 kWh per poll, so 52 repeats —
about 26 minutes of frozen frame — cross the 3.0 kWh threshold and the charge loses its
measured wallbox energy. Frozen frames of three and a half hours are already documented in
this codebase (`charge_end_from_last_charging`).

THE FIX: the car-reported figure is gated on `not stale`, exactly like the regen two lines
above it. The wallbox reading itself is NOT gated — it comes from Home Assistant, not from
the Leapmotor cloud, so it is fresh whatever the car's link is doing.

CI-safe: recorder + poller db only, no fastapi.
"""
import client
import db as poller_db
import recorder as R
from state_machine import State

FROZEN_TS = 1_787_000_000_000


def _charging_frame(ts):
    """A car charging at 7 kW: 230 V × 30.4 A. `ts` is the cloud's frame clock."""
    return client.VehicleData(
        vin="V", timestamp_ms=ts, soc=50.0, range_km=200.0, odometer_km=1000.0,
        speed_kmh=0.0, gear="P", vehicle_state="parked", charging_status=2,
        charge_power_kw=7.0, latitude=45.0, longitude=9.0, outside_temp=20.0,
        inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=20.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=True, remaining_charge_min=60,
        charge_voltage_v=230.0, charge_current_a=30.4,
    )


def _recorder_mid_charge(monkeypatch, wallbox_reading):
    """A recorder already inside a wallbox charge, with the HA counter frozen at one value.

    The counter standing still is the whole point: it is what `wb_stuck_kwh` accumulates
    against. Here it is standing still because nothing is flowing, not because it is broken.
    """
    db = poller_db.Database(":memory:")
    db._conn.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V')")
    db._conn.commit()
    charge_id = db.create_charge(1, _charging_frame(FROZEN_TS))

    rec = R.Recorder(db, vehicle_id=1)
    rec._started = True
    rec._sm.state = State.CHARGING
    rec._active_charge_id = charge_id
    rec._charge_at_wallbox = True
    rec._last_soc, rec._last_soc_ts = 50.0, "2026-08-31T10:00:00+00:00"
    rec._last_odometer = 1000.0
    monkeypatch.setattr(rec, "_read_wallbox_energy", lambda: wallbox_reading)
    # Seed the counter baseline, so the first real poll is a rise of zero rather than a seed.
    db.accumulate_wallbox_energy(charge_id, wallbox_reading, 0.0)
    return db, rec, charge_id


def _stuck(db, charge_id):
    return db._conn.execute(
        "SELECT wb_stuck_kwh FROM charges WHERE id=?", (charge_id,)).fetchone()[0] or 0.0


def test_a_repeated_frame_adds_nothing_to_the_stuck_counter(monkeypatch):
    """60 polls of the SAME frame — half an hour of a dark link — accuse nobody.

    Without the guard this is 60 × 7 kW × 30 s = 3.5 kWh, past the 3.0 threshold, and the
    charge would lose its measured wallbox energy at finalize.
    """
    db, rec, cid = _recorder_mid_charge(monkeypatch, wallbox_reading=12.0)
    rec._last_frame_ts = FROZEN_TS          # the frame below is a REPEAT of what we already saw

    for _ in range(60):
        rec.process(_charging_frame(FROZEN_TS))

    assert _stuck(db, cid) == 0.0


def test_a_fresh_frame_still_accuses_a_meter_that_stands_still(monkeypatch):
    """The guard must not disarm #215: distinct frames are real news, and a counter that
    ignores them is the stopped meter that check exists for."""
    db, rec, cid = _recorder_mid_charge(monkeypatch, wallbox_reading=12.0)
    rec._last_frame_ts = FROZEN_TS - 1

    for i in range(60):
        rec.process(_charging_frame(FROZEN_TS + i))

    assert _stuck(db, cid) > 3.0
