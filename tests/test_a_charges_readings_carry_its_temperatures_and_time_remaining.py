"""The readings of a charge, poll by poll, carry more than the power and the SoC: the coldest cell's
temperature, the minutes the car thought were left, and the outside temperature of its spot from the
weather. Not the car's range: through a charge it climbs with the SoC — on 29 real charges of a B10
the two correlate at r >= 0.998 — so a line of it would only repeat the SoC's. They line up with the power, sample for sample, so a
chart can draw them on one time axis; a reading a poll did not carry is a hole, not a zero; and a
sample after the session is not in it.
"""
import datetime as dt

import db as D
import db_reader

VIN = "LVIN0000000000001"
PLUGGED_IN = dt.datetime(2026, 9, 29, 19, 0, tzinfo=dt.timezone.utc)
CABLE_OUT = PLUGGED_IN + dt.timedelta(hours=1)


def _install(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    c = D.Database(path)._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    for key, value in (("timezone", "UTC"), ("setup_complete", "1"), ("language", "en")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,"
              "energy_added_kwh,location_type) VALUES (1,1,?,?,50,56,4.0,'HOME')",
              (PLUGGED_IN.isoformat(), CABLE_OUT.isoformat()))
    sample = ("INSERT INTO positions (vehicle_id,recorded_at,charging,charge_voltage_v,charge_current_a,"
              "soc,battery_min_temp,outside_temp,range_km,remaining_charge_min) VALUES (1,?,1,400,-10,?,?,?,?,?)")
    for k in range(7):   # every ten minutes, plug-in to cable-out; the first poll has no weather yet, the countdown stays above 0 as the poller keeps it
        c.execute(sample, ((PLUGGED_IN + dt.timedelta(minutes=10 * k)).isoformat(),
                           50 + k, 20 + k, None if k == 0 else 9.5 + k, 200 + 5 * k, 70 - 10 * k))
    c.execute(sample, ((CABLE_OUT + dt.timedelta(hours=1)).isoformat(), 99, 99, 99, 999, 999))
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)


def test_every_reading_lines_up_with_the_power(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    curve = db_reader.get_charge_power_curve(1)
    assert len(curve["power"]) == 7, "the sample an hour after the cable came out is on the chart"
    assert curve["battery_temp"] == [20, 21, 22, 23, 24, 25, 26]
    assert curve["outside_temp"] == [None, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5], \
        "a poll without a weather reading must be a hole in the line, not a value"
    assert "range_km" not in curve, "the range repeats the SoC through a charge: it is not carried"
    assert curve["remaining_min"] == [70, 60, 50, 40, 30, 20, 10]
    assert curve["soc"] == [50, 51, 52, 53, 54, 55, 56] and set(curve["power"]) == {4.0}


def test_a_charge_nobody_recorded_has_every_series_empty(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    curve = db_reader.get_charge_power_curve(404)
    assert curve == {"power": [], "soc": [], "times": [], "battery_temp": [],
                     "outside_temp": [], "remaining_min": []}
