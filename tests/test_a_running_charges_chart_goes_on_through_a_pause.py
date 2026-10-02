"""The chart of a charge in progress goes on through a pause. The car stopped from the app, or a
wallbox that took a breath, leaves the cable in and the session open; the readings keep coming
with no charging current, and until now none of them reached the chart, so it ended at the last
charging reading and told the reader nothing of the minutes since. In progress, every parked
reading since the start is on the chart, a pause as 0 kW; a finished charge's chart keeps its
charging readings alone, as before.
"""
import datetime as dt

import db as D
import db_reader

VIN = "LVIN0000000000001"
PLUGGED_IN = dt.datetime(2026, 10, 2, 15, 31, tzinfo=dt.timezone.utc)


def _install(tmp_path, monkeypatch, *, ended):
    path = str(tmp_path / "t.db")
    c = D.Database(path)._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'UTC')")
    c.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc) VALUES (10,1,?,?,87.3)",
              (PLUGGED_IN.isoformat(), (PLUGGED_IN + dt.timedelta(minutes=4)).isoformat() if ended else None))
    sample = ("INSERT INTO positions (vehicle_id,recorded_at,charging,plug_connected,charge_voltage_v,charge_current_a,"
              "soc,gear,speed_kmh) VALUES (1,?,?,1,430,?,?,'P',0)")
    for k in range(8):   # five polls charging at 4 A, then the car stopped: flag 0, a 1.1 A draw
        charging = k < 5
        c.execute(sample, ((PLUGGED_IN + dt.timedelta(seconds=30 * k)).isoformat(),
                           1 if charging else 0, -4.0 if charging else 1.1, 87.3 + 0.1 * k))
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)


def test_in_progress_the_pause_is_on_the_chart_as_zero(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, ended=False)
    curve = db_reader.get_charge_power_curve(10)
    assert len(curve["times"]) == 8, "the readings since the car stopped are on the chart"
    assert curve["power"][:5] == [1.72] * 5 and curve["power"][5:] == [0.0, 0.0, 0.0], \
        "a pause is 0 kW, not the car's own draw read as charging power"
    assert curve["soc"][-1] == 88.0, "the SoC goes on through the pause"


def test_a_finished_charge_keeps_its_charging_readings_alone(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, ended=True)
    curve = db_reader.get_charge_power_curve(10)
    assert len(curve["times"]) == 5 and curve["power"] == [1.72] * 5
