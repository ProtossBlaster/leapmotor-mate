"""What the poller reads of the home wallbox during a charge is kept in Mate's own database.

The chart of a home charge draws the wallbox's power beside the car's, and the comparison on the Wallbox page
rests on the same line. It was read back from Home Assistant's history on every opening, and Home Assistant
purges that history after its recorder's retention, ten days by default: the line of an older charge was gone
for good. The poller reads the wallbox every poll of a home charge anyway, for the counter the charge is
billed on; the reading goes into `ha_samples`, dated by the poll's own position row so it lines up with the
car's readings, and the chart draws from there (see test_the_charge_chart_draws_the_wallbox_from_mates_samples).
"""
from datetime import timedelta

import diagnostics
import pytest
from schema import WALLBOX_ENERGY_KWH, WALLBOX_POWER_KW
from test_a_trip_ends_when_the_car_last_spoke import _ms, _vd, rig  # noqa: F401  (rig: fixture)


def _charging(wall, soc):
    d = _vd(ts=_ms(wall["now"]), soc=soc, gear="P", speed=0.0)
    d.plug_connected, d.charging_status = True, 1
    d.charge_power_kw, d.charge_current_a, d.charge_voltage_v = 7.0, -17.5, 400.0
    return d


def _samples(db):
    return [tuple(r) for r in db._conn.execute(
        "SELECT recorded_at, kind, value FROM ha_samples ORDER BY recorded_at, kind").fetchall()]


@pytest.fixture
def home(rig, monkeypatch):
    """A charge at the wallbox, the wallbox answering what `reading` holds while the poll runs."""
    db, rec, poll, wall = rig
    reading = {}
    monkeypatch.setattr(rec, "_read_wallbox", lambda: dict(reading))    # the same answer however often a poll asks

    def charge_poll(new, seconds=30, soc=50.0):
        reading.clear()
        reading.update(new)
        wall["now"] += timedelta(seconds=seconds)
        rec.process(_charging(wall, soc))
        return wall["now"].isoformat()

    return db, rec, charge_poll, wall


def test_each_poll_of_a_home_charge_keeps_the_power_and_the_counter_with_its_row(home):
    db, _rec, charge_poll, _wall = home
    t1 = charge_poll({"energy_kwh": 100.0, "power_kw": 7.1})
    t2 = charge_poll({"energy_kwh": 100.06, "power_kw": 7.2})

    assert _samples(db) == [(t1, WALLBOX_ENERGY_KWH, 100.0), (t1, WALLBOX_POWER_KW, 7.1),
                            (t2, WALLBOX_ENERGY_KWH, 100.06), (t2, WALLBOX_POWER_KW, 7.2)]
    rows = db._conn.execute("SELECT recorded_at FROM positions WHERE charging = 1 ORDER BY recorded_at").fetchall()
    assert [r[0] for r in rows] == [t1, t2]                   # the samples are dated by the poll's own row


def test_what_was_not_read_leaves_no_row(home):
    db, _rec, charge_poll, _wall = home
    charge_poll({})                                            # Home Assistant did not answer
    t = charge_poll({"energy_kwh": 100.0, "power_kw": None})  # the power entity unavailable

    assert _samples(db) == [(t, WALLBOX_ENERGY_KWH, 100.0)]


def test_a_charge_away_from_the_wallbox_reads_nothing(home, monkeypatch):
    db, _rec, charge_poll, _wall = home
    monkeypatch.setattr(db, "wallbox_energy_applies", lambda vid, lat, lon: False)
    charge_poll({"energy_kwh": 100.0, "power_kw": 7.1})

    assert _samples(db) == []


def test_a_reading_taken_while_the_car_is_silent_is_kept_too(home):
    """The meter is read on a cycle the car said nothing in (#295); that reading has no row of the car's to
    sit on and is dated by its own clock."""
    db, rec, charge_poll, wall = home
    charge_poll({"energy_kwh": 100.0, "power_kw": 7.1})
    wall["now"] += timedelta(seconds=60)                      # a cycle later, no frame from the car
    rec._read_wallbox = lambda: {"energy_kwh": 100.5, "power_kw": 7.0}
    rec.sample_wallbox_meter()

    last = db._conn.execute("SELECT MAX(recorded_at) FROM ha_samples").fetchone()[0]
    assert _samples(db)[-2:] == [(last, WALLBOX_ENERGY_KWH, 100.5), (last, WALLBOX_POWER_KW, 7.0)]
    assert last > db._conn.execute("SELECT MAX(recorded_at) FROM positions").fetchone()[0]


def test_the_bundle_counts_them(home, monkeypatch):
    db, _rec, charge_poll, _wall = home
    charge_poll({"energy_kwh": 100.0, "power_kw": 7.1})
    import db_reader
    monkeypatch.setattr(db_reader, "DB_PATH", db._conn.execute("PRAGMA database_list").fetchone()[2])
    assert "ha_samples=2" in diagnostics.build_bundle("9.9.9")
