"""A charge sample is one the car was TAKING current on, not only one it flagged.

@arzthilfe, #341. The 4.7.1 fix (a merged charge's curve covers the whole plug-in) is a real defect
but it is not his: every one of his 42 charges is a single row, `merged=—`. Measured on the
diagnostics bundle he attached on 29/09, night of 28→29 September — the session his screenshot shows,
22:17 → 07:09:

    22:17:21  plug=1 chg=1 A=-2.3      <- wallbox at 11 A
    22:17:52  plug=1 chg=0 A=-1.7      <- turned down to 8 A: the FLAG drops, the current does not

and then `chg=0` with `A=-1.6/-1.7` all night, `State: charging` throughout, SoC 78.9 → 92.2.
146 of that session's 1177 polls carry the flag: 12.4 %. His chart stopped at 22:17 because that is
the minute the flag went, not the minute the charge did.

Why the flag goes: `poller/client._is_charging` needs `abs(pack current) >= _CHARGE_CURRENT_MIN_A`
(the user's charge-detection setting, 2.0 A by default). At 8 A his C10 reports 1.7 A on signal 1178,
below the floor, so `positions.charging` is written 0. The SESSION stays open because the state
machine also holds it on the cable (`charge_active or plug_connected`) — which is why there is one
row and no 🔗. Only the queries that read the session back by the raw flag lost the night.

🔑 Inside a session's window, a NEGATIVE pack current IS charge, whatever the flag says. The two
extra gates are the ones `_is_charging` uses, for the same reason: a car in gear or moving has a
strongly negative pack current from regen, and an orphan session whose `ended_at` bled forward would
otherwise pull that in. Measured on a real 375k-row database: the motion gate excludes 8580 of the
8581 unflagged negative-current rows, and the one it keeps is a sample 69 s inside a session whose
flag had not caught up yet — one that SHOULD count.
"""
import datetime as dt

import pytest

import db as D
import db_reader

VIN = "LVIN0000000000001"
PLUGGED_IN = dt.datetime(2026, 9, 28, 20, 17, tzinfo=dt.timezone.utc)
TURNED_DOWN = dt.datetime(2026, 9, 28, 22, 17, 52, tzinfo=dt.timezone.utc)   # 11 A → 8 A
CABLE_OUT = dt.datetime(2026, 9, 29, 5, 9, tzinfo=dt.timezone.utc)
VOLTS = 721.6          # signal 1177 on his car, from the bundle's raw-signal block
AMPS_11, AMPS_8 = -2.3, -1.7


def _install(tmp_path, monkeypatch, *, extra_rows=()):
    """His session: ONE charge row, the flag on for two hours and off for the other seven."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (VIN,))
    for key, value in (("timezone", "UTC"), ("setup_complete", "1"), ("language", "en")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,"
              "energy_added_kwh,location_type) VALUES (1,1,?,?,78.9,92.2,10.9,'HOME')",
              (PLUGGED_IN.isoformat(), CABLE_OUT.isoformat()))
    t = PLUGGED_IN
    while t <= CABLE_OUT:
        flagged = t < TURNED_DOWN
        c.execute("INSERT INTO positions (vehicle_id,recorded_at,charging,plug_connected,"
                  "charge_voltage_v,charge_current_a,soc,speed_kmh,gear) "
                  "VALUES (1,?,?,1,?,?,80,0,'P')",
                  (t.isoformat(), 1 if flagged else 0, VOLTS, AMPS_11 if flagged else AMPS_8))
        t += dt.timedelta(minutes=5)
    for sql, params in extra_rows:
        c.execute(sql, params)
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    return path


def _kw(amps):
    return round(abs(VOLTS * amps) / 1000.0, 3)


def test_the_curve_does_not_stop_where_the_flag_stopped(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    curve = db_reader.get_charge_power_curve(1)
    assert curve["times"], "the session has no curve at all"
    last = curve["times"][-1]
    assert last >= (CABLE_OUT - dt.timedelta(minutes=5)).isoformat(), (
        f"the curve stops at {last}, the flag stopped at {TURNED_DOWN.isoformat()}, "
        f"the cable came out at {CABLE_OUT.isoformat()}")


def test_both_power_levels_are_drawn(tmp_path, monkeypatch):
    """The reported symptom: the 8 A half of the night was not on the chart."""
    _install(tmp_path, monkeypatch)
    levels = set(db_reader.get_charge_power_curve(1)["power"])
    assert _kw(AMPS_8) in levels, f"the 8 A stretch ({_kw(AMPS_8)} kW) is missing: drawn {sorted(levels)}"
    assert _kw(AMPS_11) in levels, f"the 11 A stretch ({_kw(AMPS_11)} kW) is missing"


def test_the_session_window_reaches_the_end(tmp_path, monkeypatch):
    """`_charge_active_window` is what the wallbox comparison and the HOME cost are aligned on."""
    _install(tmp_path, monkeypatch)
    db = db_reader._get()
    start, end = db_reader._charge_active_window(db, PLUGGED_IN.isoformat(), CABLE_OUT.isoformat())
    assert start and end, "the session has no active window"
    assert end >= (CABLE_OUT - dt.timedelta(minutes=5)).isoformat(), \
        f"the active window ends at {end}, seven hours before the cable came out"


def test_a_pause_is_still_not_a_charging_sample(tmp_path, monkeypatch):
    """A wallbox that stops the current reads a small POSITIVE pack current (his 04:22: A=0.1).
    That is the car resting on the cable, and it must stay off the chart."""
    # Off the five-minute grid ON PURPOSE: a sample at one of the loop's own timestamps would be
    # drawn because of the loop's row, and the test would report a defect that is its own doing.
    paused = (CABLE_OUT - dt.timedelta(minutes=2, seconds=-13)).isoformat()
    _install(tmp_path, monkeypatch, extra_rows=[
        ("INSERT INTO positions (vehicle_id,recorded_at,charging,plug_connected,charge_voltage_v,"
         "charge_current_a,soc,speed_kmh,gear) VALUES (1,?,0,1,?,0.1,92,0,'P')", (paused, VOLTS)),
    ])
    assert paused not in db_reader.get_charge_power_curve(1)["times"], \
        "a resting sample on the cable was drawn as charging power"


def test_regen_inside_a_bled_window_is_not_a_charging_sample(tmp_path, monkeypatch):
    """An abandoned session's `ended_at` can bleed past the drive that followed it
    (poller.close_orphan_charges). Regen is strongly negative and shares the sign of charge, so the
    motion gate is what keeps a drive out of a charge's curve."""
    driving = (TURNED_DOWN + dt.timedelta(minutes=1, seconds=7)).isoformat()
    _install(tmp_path, monkeypatch, extra_rows=[
        ("INSERT INTO positions (vehicle_id,recorded_at,charging,plug_connected,charge_voltage_v,"
         "charge_current_a,soc,speed_kmh,gear) VALUES (1,?,0,0,?,-42.0,80,63,'D')", (driving, VOLTS)),
    ])
    curve = db_reader.get_charge_power_curve(1)
    assert driving not in curve["times"], "a regen sample from a drive was drawn as charging power"
    assert _kw(-42.0) not in set(curve["power"]), "the drive's regen power reached the chart"


def test_the_charge_is_listed_as_having_power(tmp_path, monkeypatch):
    """`charges_with_power` / `latest_charge_id_with_power` feed the Wallbox page's chart picker."""
    _install(tmp_path, monkeypatch)
    assert db_reader.latest_charge_id_with_power() == 1
    assert 1 in [c["id"] for c in db_reader.charges_with_power(limit=5)]


@pytest.mark.parametrize("amps", [-0.4, -0.2, 0.0])
def test_a_current_below_the_noise_floor_is_not_a_charge(tmp_path, monkeypatch, amps):
    """Not every non-zero reading is energy. On a real 375k-row history NO parked sample sits
    between -0.5 A and 0, so the floor is below everything measured rather than fitted to it."""
    quiet = (CABLE_OUT - dt.timedelta(minutes=1, seconds=-21)).isoformat()
    _install(tmp_path, monkeypatch, extra_rows=[
        ("INSERT INTO positions (vehicle_id,recorded_at,charging,plug_connected,charge_voltage_v,"
         "charge_current_a,soc,speed_kmh,gear) VALUES (1,?,0,1,?,?,92,0,'P')", (quiet, VOLTS, amps)),
    ])
    assert quiet not in db_reader.get_charge_power_curve(1)["times"], \
        f"{amps} A was drawn as charging power"
