"""A trip's detail splits its duration into the time driving, the time stopped, and what is unknown.

Both are taken from Mate's own readings: the span after each reading counts as driving above walking
pace and as stopped at or below it — lights, queues. A span counts only inside one recorded piece of
the trip, cut to that piece's start and end, and only when it is no longer than three usual steps;
anything else is a hole in the recording, and the part of the duration no span covers is reported as
unknown, never given to either. The three are whole minutes that add up to the duration printed above them.
"""
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest
import recorder as R
import state_machine as SM
from client import VehicleData

START = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)


def _install(tmp_path, monkeypatch, pieces):
    """`pieces` = [(start_minute, duration_min, {minute: km/h})]; the pieces after the first are
    joined into it. One reading per listed minute."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    for key, value in (("is_reev", "0"), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    for tid, (start, duration, speeds) in enumerate(pieces, start=1):
        a = START + timedelta(minutes=start)
        c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc,"
                  " end_soc, merged_into_id) VALUES (?,1,?,?,8.0,?,80,78,?)",
                  (tid, a.isoformat(), (a + timedelta(minutes=duration)).isoformat(), duration,
                   1 if tid > 1 else None))
        for minute, kmh in speeds.items():
            c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
                      " VALUES (?,?,?,9.0,?,?)", (tid, (START + timedelta(minutes=minute)).isoformat(),
                                                  45.0 + minute * 0.01, kmh, 80 - minute * 0.1))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def _split(trip):
    return tuple(round(trip[k], 3) for k in ("driving_min", "stopped_min", "unknown_min"))


CITY = [(0, 9, {0: 0, 1: 0, 2: 30, 3: 50, 4: 0, 5: 0, 6: 0, 7: 40, 8: 60, 9: 0})]


def test_the_minutes_moving_and_at_a_standstill(tmp_path, monkeypatch):
    assert _split(_install(tmp_path, monkeypatch, CITY).get_trip_detail(1)) == (4, 5, 0)


def test_a_hole_in_the_recording_counts_as_neither(tmp_path, monkeypatch):
    """Readings stop at minute 4 and come back at minute 20: those 16 minutes are nobody's."""
    speeds = {m: 0 if m in (1, 21) else 50 for m in list(range(5)) + list(range(20, 25))}
    trip = _install(tmp_path, monkeypatch, [(0, 25, speeds)]).get_trip_detail(1)
    assert _split(trip) == (6, 2, 17)


def test_the_stop_between_joined_pieces_is_not_a_standstill(tmp_path, monkeypatch):
    """Two ten-minute pieces a reading a minute apart, joined across a two-minute stop, the first
    ending on a standstill reading: the stop is shorter than three steps and still counts as nothing."""
    first = {m: 0 if m == 9 else 50 for m in range(10)}
    second = {m: 50 for m in range(12, 22)}
    trip = _install(tmp_path, monkeypatch, [(0, 10, first), (12, 10, second)]).get_trip_detail(1)
    assert trip["stopped_min"] == 0
    assert _split(trip) == (18, 0, 2)


def test_the_parts_add_up_to_the_duration_printed_above_them(tmp_path, monkeypatch):
    """12.7 min driving, 5.1 stopped, 1.5 without readings: rounded one by one they print 13 + 5 + 2
    under a duration of 19. The minute that does not fit goes to the part that lost the most."""
    speeds = {k / 10: 50 if k < 127 else 0 for k in range(179)}
    trip = _install(tmp_path, monkeypatch, [(0, 19.3, speeds)]).get_trip_detail(1)
    assert (trip["driving_min"], trip["stopped_min"], trip["unknown_min"]) == (13, 5, 1)


def _frame(ts, km):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=int(ts.timestamp() * 1000), soc=80.0, range_km=300, odometer_km=1000 + km,
        speed_kmh=50.0, gear="D", vehicle_state="driving", charging_status=0, charge_power_kw=0.0,
        latitude=45.0 + km * 0.01, longitude=9.0, outside_temp=None, inside_temp=20.0, climate_target_temp=21.0,
        battery_min_temp=15.0, is_locked=True, climate_on=False, climate_cooling=False,
        climate_heating=False, climate_defrost=False, trunk_open=False, windows_open=False,
        sunshade_open=False, any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0)


def test_the_readings_after_the_trips_end_are_not_counted(tmp_path, monkeypatch):
    """The recorder ends a trip on the car's own clock, and stamps its points with ours. With the car
    45 s behind and the cloud then repeating one frame until the trip is closed, the trip lasts 4.25
    minutes and its points span 5: the part after its end belongs to neither."""
    wall, mono = {"now": START}, {"t": 10_000.0}
    monkeypatch.setattr(SM.time, "monotonic", lambda: mono["t"])
    monkeypatch.setattr(D, "_now_iso", lambda: wall["now"].isoformat())
    monkeypatch.setattr(R, "_now_iso", lambda: wall["now"].isoformat())
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb.set_battery_capacity(65.0)
    pdb.set_setting("timezone", "UTC")
    rec = R.Recorder(pdb, vehicle_id=pdb.ensure_vehicle("TESTVIN", "B10"))
    car = START - timedelta(seconds=45)

    def poll(seconds, frame):
        wall["now"] += timedelta(seconds=seconds)
        mono["t"] += seconds
        rec.process(frame)

    poll(0, _frame(car, 0))
    for minute in range(1, 6):
        poll(60, _frame(car + timedelta(minutes=minute), minute))
    for _ in range(31 * 6):
        poll(10, _frame(car + timedelta(minutes=5), 5))
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    trip = db_reader.get_trip_detail(pdb._conn.execute("SELECT MAX(id) FROM trips").fetchone()[0])
    assert trip["duration_min"] == pytest.approx(4.25, abs=0.05)
    assert trip["driving_min"] + trip["stopped_min"] <= trip["duration_min"]
    assert trip["driving_min"] + trip["stopped_min"] + trip["unknown_min"] == 4


def _duration_row(tmp_path, monkeypatch, pieces):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _install(tmp_path, monkeypatch, pieces)
    html = TestClient(main.app).get("/trips/1").text
    row = re.search(r'<span class="text-slate-400">Duration\b.*?</div>', html, re.DOTALL).group(0)
    return row, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row))


def test_the_page_prints_the_split_under_the_duration(tmp_path, monkeypatch):
    row, text = _duration_row(tmp_path, monkeypatch, CITY)
    assert "9 min" in text and "4 min driving · 5 min stopped" in text and "no data" not in text
    assert re.search(r'<span class="text-slate-400">Duration <span[^>]*data-tip="[^"]*driving[^"]*stopped'
                     r'[^"]*no data', row), "the info mark is not beside the label, or leaves part of the split out"
    assert "stopped ⓘ" not in text


def test_the_page_names_what_it_does_not_know(tmp_path, monkeypatch):
    speeds = {m: 0 if m in (1, 21) else 50 for m in list(range(5)) + list(range(20, 25))}
    row, text = _duration_row(tmp_path, monkeypatch, [(0, 25, speeds)])
    assert "6 min driving · 2 min stopped" in text and "17 min no data" in text
    assert re.search(r'block[^"]*">17 min no data</span>', row), "no data is not a line of its own"


def test_the_page_prints_parts_that_add_up(tmp_path, monkeypatch):
    speeds = {k / 10: 50 if k < 127 else 0 for k in range(179)}
    _, text = _duration_row(tmp_path, monkeypatch, [(0, 19.3, speeds)])
    assert "19 min" in text and "13 min driving · 5 min stopped" in text and "1 min no data" in text


def test_the_parts_add_up_even_when_every_one_of_them_is_nothing():
    """The splitter shares whole minutes out onto the duration by scaling the parts onto it. Parts
    that are ALL zero have nothing to scale, and the leftover was then handed out one minute per
    part instead of being accounted for: (0, 0) onto 95 came back as [1, 1]. No caller can produce
    that today — the unknown band takes whatever the two others leave — so this is the helper being
    made honest on its own, not a fix for something on screen."""
    for parts, total in (((0.0, 0.0), 95), ((0.0, 0.0, 0.0), 7), ((0, 0, 0), 1)):
        out = db_reader._whole_minutes(parts, total)
        assert sum(out) == total, (parts, total, out)
        assert all(isinstance(v, int) and v >= 0 for v in out), (parts, total, out)
