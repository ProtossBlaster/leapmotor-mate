"""Two ways one physical charge stops being one row in the database.

Both were found by the 30/08 audit, both were reproduced against the real Recorder and Database,
and neither made a single test fail — the suite was green with both of them in.

They share a cause: `process()` runs the state machine's events BEFORE the reconstruction pass, and
the reconstruction guard reads state that those events have just changed.

  1. DOUBLE. `_handle_event` finalizes a closing charge and clears `_active_charge_id`; twenty lines
     later `_maybe_reconstruct_charge` looks at that same poll, finds "parked, nothing open, SoC far
     above the baseline" and files the charge a SECOND time as reconstructed. The guard did not
     fail — it was answered after the answer had changed.

  2. FUSED. The close only happens on CHARGING -> parked or -> DRIVING. A charge that ends while the
     cloud is unreachable goes CHARGING -> OFFLINE -> PARKED_ACTIVE, which is neither, so the row
     dangles open — and the next plug-in "resumes" it, because re-entering CHARGING with a charge
     open is read as "we never unplugged". True normally; false after an outage.

CI-safe: recorder + poller db only, no fastapi.
"""
import client
import db as poller_db
import recorder as R
from state_machine import State

TS = 1_787_100_000_000


def _frame(ts, soc, charging, power=0.0, plug=True):
    return client.VehicleData(
        vin="V", timestamp_ms=ts, soc=soc, range_km=200.0, odometer_km=1000.0,
        speed_kmh=0.0, gear="P", vehicle_state="parked",
        charging_status=(2 if charging else 0), charge_power_kw=power,
        latitude=45.0, longitude=9.0, outside_temp=20.0, inside_temp=20.0,
        climate_target_temp=21.0, battery_min_temp=20.0, is_locked=True,
        climate_on=False, climate_cooling=False, climate_heating=False, climate_defrost=False,
        trunk_open=False, windows_open=False, sunshade_open=False, any_door_open=False,
        plug_connected=plug, remaining_charge_min=0,
        charge_voltage_v=(230.0 if charging else 0.0),
        charge_current_a=(30.4 if charging else 0.0),
    )


def _recorder():
    db = poller_db.Database(":memory:")
    db._conn.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V')")
    db._conn.commit()
    rec = R.Recorder(db, vehicle_id=1)
    rec._started = True
    rec._last_odometer = 1000.0
    return db, rec


def _charges(db):
    return db._conn.execute(
        "SELECT id, start_soc, end_soc, energy_added_kwh, reconstructed FROM charges "
        "ORDER BY id").fetchall()


# ── 1. the same energy, filed twice ─────────────────────────────────────────────
def test_a_closing_charge_is_not_also_reconstructed():
    """One physical charge, 40 → 78, whose second half ran behind a frozen cloud frame.

    The live row is right. The reconstructed one on top of it is the same energy again — and the
    charge count, the kWh totals, the calendar and the monthly report all carry it twice.
    """
    db, rec = _recorder()
    rec._sm.state = State.CHARGING
    rec._active_charge_id = db.create_charge(1, _frame(TS, 40.0, True, 7.0))
    # The baseline the reconstruction compares against: the last poll that got through.
    rec._last_soc, rec._last_soc_ts = 46.0, "2026-08-31T01:00:00+00:00"

    # The link comes back on the poll where the charge is already over: cable out, SoC at 78.
    rec.process(_frame(TS + 1, 78.0, False, 0.0, plug=False))

    rows = _charges(db)
    assert len(rows) == 1, f"one charge, filed once — got {[tuple(r) for r in rows]}"


# ── 2. two plug-ins, one row ────────────────────────────────────────────────────
def test_a_charge_that_ended_in_the_dark_does_not_swallow_the_next_one():
    """The charge ends and the cable comes out while the cloud is refusing logins. When the link
    returns the car is parked and unplugged — a state the close never watches for. The row stays
    open, and the next plug-in is recorded inside it: two sessions, possibly at two places and two
    price bases, carrying the first one's GPS, wallbox attribution and start SoC.
    """
    db, rec = _recorder()
    rec._sm.state = State.CHARGING
    rec._active_charge_id = db.create_charge(1, _frame(TS, 40.0, True, 7.0))
    rec._last_soc, rec._last_soc_ts = 40.0, "2026-08-31T01:00:00+00:00"

    for _ in range(3):                       # the cloud refuses: three errors → OFFLINE
        rec._sm.mark_offline()
    rec.process(_frame(TS + 1, 60.0, False, 0.0, plug=False))   # back, parked, cable out

    assert rec._active_charge_id is None, "the charge ended while the link was dark — close it"

    rec.process(_frame(TS + 2, 60.0, True, 7.0))                # a NEW plug-in, later
    rec.process(_frame(TS + 3, 61.0, True, 7.0))

    rows = _charges(db)
    assert len(rows) == 2, f"two plug-ins, two rows — got {[tuple(r) for r in rows]}"


# ── and the two things those guards must NOT break ──────────────────────────────
def test_a_charge_that_happened_while_asleep_is_still_reconstructed():
    """The reconstruction exists for #29: a charge that ran start to finish with the car asleep to
    the cloud, seen only as a SoC that jumped. Skipping the poll that CLOSED a charge must not
    disarm the case the pass was written for."""
    db, rec = _recorder()
    rec._sm.state = State.PARKED_ACTIVE
    rec._last_soc, rec._last_soc_ts = 40.0, "2026-08-31T01:00:00+00:00"

    rec.process(_frame(TS + 1, 78.0, False, 0.0, plug=False))     # woke up much fuller

    rows = _charges(db)
    assert len(rows) == 1 and rows[0]["reconstructed"] == 1, \
        f"the missed charge must still be caught — got {[tuple(r) for r in rows]}"


def test_coming_back_from_the_dark_with_the_cable_still_in_closes_nothing():
    """A flat frame with the cable still connected is a PAUSE, not an ending — a modulating
    wallbox produces exactly that, and the live path owns it. Closing there would fragment one
    plug-in into several rows, which is the bug the resume was written to prevent (#23)."""
    db, rec = _recorder()
    rec._sm.state = State.CHARGING
    rec._active_charge_id = db.create_charge(1, _frame(TS, 40.0, True, 7.0))
    rec._last_soc, rec._last_soc_ts = 40.0, "2026-08-31T01:00:00+00:00"
    open_id = rec._active_charge_id

    for _ in range(3):
        rec._sm.mark_offline()
    rec.process(_frame(TS + 1, 50.0, False, 0.0, plug=True))       # back, but still plugged in

    assert rec._active_charge_id == open_id, "cable still in — the charge is paused, not over"
