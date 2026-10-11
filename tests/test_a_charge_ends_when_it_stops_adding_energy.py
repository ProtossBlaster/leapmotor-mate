"""A charge ends when it stops adding energy, not when the car later ends its charge session.

A car ends its charge session some time after the energy stops, with the cable still in: on 1149, the
charge state, a B10 sends 2 when the charge ends and 0 when the session does. The row is closed with the
session, but nothing flowed in between, and that time is not part of the charge. A car that reports no
current while it charges (a range-extender on AC) ends when its SoC stops rising instead.
"""
from datetime import timedelta

from test_a_trip_ends_when_the_car_last_spoke import _ms, _vd, rig  # noqa: F401  (rig: fixture)


def _frame(wall, soc, *, charging, plug=True, amps=None, reev=False):
    d = _vd(ts=_ms(wall["now"]), soc=soc, gear="P", speed=0.0)
    d.plug_connected = plug
    if charging:
        d.charging_status, d.charge_power_kw, d.charge_current_a, d.charge_voltage_v = 1, 1.7, -3.9, 440.0
    else:
        d.charge_power_kw, d.charge_current_a, d.charge_voltage_v = 0.5, 1.1, 440.0
    if amps is not None:
        d.charge_current_a = amps
    if reev:
        d.is_reev = True
    return d


def _read(rig, seconds, soc, *, charging, plug=True, amps=None, reev=False):
    _db, _rec, poll, wall = rig
    wall["now"] += timedelta(seconds=seconds)
    poll(0, _frame(wall, soc, charging=charging, plug=plug, amps=amps, reev=reev))
    return wall["now"]


def _charge(db):
    return dict(db._conn.execute("SELECT * FROM charges").fetchone())


def test_a_charge_full_at_last_ends_when_the_current_stopped(rig):
    db = rig[0]
    started = _read(rig, 0, 96.0, charging=True)
    for soc in (96.5, 97.0, 97.3):
        _read(rig, 600, soc, charging=True)
    stopped = _read(rig, 30, 100.0, charging=False)          # current gone, the cable still reads in
    for _ in range(22):                                       # the session stays open a while
        _read(rig, 30, 100.0, charging=False)
    closed = _read(rig, 30, 100.0, charging=False, plug=False)

    charge = _charge(db)
    assert charge["ended_at"] == stopped.isoformat()
    assert charge["closed_at"] == closed.isoformat()          # the session's close, kept for what reads the session
    assert charge["duration_min"] == round((stopped - started).total_seconds() / 60, 1)
    assert charge["end_soc"] == 100.0
    assert charge["close_reason"] == "unplugged"


def test_the_end_soc_is_the_one_read_at_the_end(rig):
    db = rig[0]
    _read(rig, 0, 80.0, charging=True)
    _read(rig, 600, 87.0, charging=True)
    stopped = _read(rig, 30, 87.4, charging=False)
    for _ in range(60):                                       # the battery settles a tenth down while the session stays open
        _read(rig, 30, 87.3, charging=False)
    _read(rig, 30, 87.3, charging=False, plug=False)

    charge = _charge(db)
    assert charge["ended_at"] == stopped.isoformat()
    assert charge["end_soc"] == 87.4
    assert charge["energy_added_kwh"] == round((87.4 - 80.0) / 100 * 65.0, 3)    # the energy follows it


def test_a_reading_without_an_soc_is_not_the_end(rig):
    """A row left without an SoC (an old repair blanks bogus zeros) cannot date the end; the first with one does."""
    db = rig[0]
    _read(rig, 0, 78.0, charging=True)
    _read(rig, 600, 80.0, charging=True)
    _read(rig, 30, 80.0, charging=False)
    db._conn.execute("UPDATE positions SET soc=NULL WHERE recorded_at=(SELECT MAX(recorded_at) FROM positions)")
    db._conn.commit()
    stopped = _read(rig, 30, 80.0, charging=False)
    for _ in range(10):
        _read(rig, 30, 80.0, charging=False)
    _read(rig, 30, 80.0, charging=False, plug=False)

    charge = _charge(db)
    assert charge["ended_at"] == stopped.isoformat()
    assert charge["end_soc"] == 80.0


def test_a_pause_inside_the_charge_does_not_end_it_there(rig):
    db = rig[0]
    _read(rig, 0, 50.0, charging=True)
    _read(rig, 600, 53.0, charging=False)                     # the wallbox holds the current back…
    _read(rig, 60, 53.0, charging=False)
    _read(rig, 60, 53.0, charging=True)                       # …and gives it back
    _read(rig, 600, 56.0, charging=True)
    stopped = _read(rig, 30, 56.0, charging=False)
    _read(rig, 30, 56.0, charging=False, plug=False)

    assert _charge(db)["ended_at"] == stopped.isoformat()


def test_current_under_the_detection_floor_is_still_the_charge(rig):
    """The car drops its charging flag once the pack draws under 2 A, while current still goes in (#341)."""
    db = rig[0]
    _read(rig, 0, 78.0, charging=True)
    _read(rig, 600, 80.0, charging=True)
    for _ in range(5):
        _read(rig, 60, 80.0, charging=False, amps=-1.6)
    stopped = _read(rig, 60, 80.0, charging=False)
    _read(rig, 30, 80.0, charging=False, plug=False)

    assert _charge(db)["ended_at"] == stopped.isoformat()


def test_current_still_going_in_when_the_session_closes_ends_the_charge_there(rig):
    db = rig[0]
    _read(rig, 0, 58.0, charging=True)
    _read(rig, 600, 60.0, charging=True)
    _read(rig, 600, 60.0, charging=False, amps=-1.6)
    closed = _read(rig, 600, 60.0, charging=False, plug=False, amps=-1.6)

    assert _charge(db)["ended_at"] == closed.isoformat()


def test_a_cable_pulled_while_charging_ends_the_charge_on_that_reading(rig):
    db = rig[0]
    _read(rig, 0, 50.0, charging=True)
    pulled = _read(rig, 600, 53.0, charging=False, plug=False)

    assert _charge(db)["ended_at"] == pulled.isoformat()


def test_a_charge_the_car_reports_no_current_for_ends_when_its_soc_stopped_rising(rig):
    """A range-extender charging AC reads no current at all; its SoC rising is the charge."""
    db = rig[0]
    for soc in (50.0, 50.2, 50.4, 52.0, 55.0, 58.0):
        _read(rig, 600, soc, charging=False, reev=True)
    full = _read(rig, 600, 60.0, charging=False, reev=True)
    for _ in range(30):
        _read(rig, 60, 60.0, charging=False, reev=True)
    _read(rig, 60, 60.0, charging=False, plug=False, reev=True)

    assert _charge(db)["ended_at"] == full.isoformat()


def test_a_soc_that_dips_and_rises_again_is_still_the_charge(rig):
    """At its limit the car's own systems draw the battery down a point, and the charger brings it back."""
    db = rig[0]
    for soc in (55.0, 57.0, 59.0, 60.0):
        _read(rig, 600, soc, charging=False, reev=True)
    for soc in (60.0, 59.5, 59.0, 59.0, 59.5):
        _read(rig, 600, soc, charging=False, reev=True)
    again = _read(rig, 600, 60.0, charging=False, reev=True)
    for _ in range(30):
        _read(rig, 60, 60.0, charging=False, reev=True)
    _read(rig, 60, 60.0, charging=False, plug=False, reev=True)

    assert _charge(db)["ended_at"] == again.isoformat()


def test_the_wallbox_total_is_measured_over_the_session_not_the_charge(rig, monkeypatch):
    """The counter is read until the car ends its session, and what the wallbox fed the car's own systems after
    the battery was full is billed energy: the plausibility ceiling spans the time the counter was read."""
    db, rec, _poll, _wall = rig
    meter = {"kwh": 100.0}
    monkeypatch.setattr(rec, "_read_wallbox", lambda: {"energy_kwh": meter["kwh"]})
    _read(rig, 0, 99.0, charging=True)
    for _ in range(6):
        meter["kwh"] += 1.8 * 30 / 3600
        _read(rig, 30, 99.5, charging=True)
    stopped = _read(rig, 30, 100.0, charging=False)
    for _ in range(120):                                      # two hours on the cable, 2 kW into the car's own systems
        meter["kwh"] += 2.0 / 60
        _read(rig, 60, 100.0, charging=False)
    _read(rig, 60, 100.0, charging=False, plug=False)

    charge = _charge(db)
    assert charge["ended_at"] == stopped.isoformat()
    assert charge["ac_energy_kwh"] >= 4.0
