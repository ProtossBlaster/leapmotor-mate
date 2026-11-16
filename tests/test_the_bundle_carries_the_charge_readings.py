"""The bundle carries the readings behind the latest charge charts, so a charge can be looked at
reading by reading without a screenshot: where the power dropped, when the battery warmed, where
the readings stop. The bundle had the charge's summary row only, and the log's times to go by (#431).

The last ten charges of each kind, newest first and however old: the nightly home charges must not
push the one road-trip charge out. The values are the chart's own, and no place beyond its kind.
"""
import datetime as dt

import db as D
import db_reader
import diagnostics
import pytest

VIN = "LVIN0000000000001"
T0 = dt.datetime(2026, 11, 1, 21, 0, tzinfo=dt.timezone.utc)
LAT, LON = 45.4642035, 9.1899817


@pytest.fixture
def install(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    c = D.Database(path)._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','UTC')")
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)

    def charge(cid, start, kind="AC", minutes=20, merged_into=None, kwh=None, peak=None, ended=True):
        end = start + dt.timedelta(minutes=minutes)
        c.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type,"
                  "location_type,merged_into_id,energy_added_kwh,max_power_kw) VALUES (?,1,?,?,20,40,?,?,?,?,?)",
                  (cid, start.isoformat(), end.isoformat() if ended else None, kind,
                   "HOME" if kind == "AC" else "FAST",
                   merged_into, kwh, peak))
        t, i = start, 0
        while t <= end:
            c.execute("INSERT INTO positions (vehicle_id,recorded_at,latitude,longitude,charging,"
                      "charge_voltage_v,charge_current_a,soc,battery_min_temp,outside_temp,"
                      "remaining_charge_min) VALUES (1,?,?,?,1,400,?,?,?,4.5,?)",
                      (t.isoformat(), LAT, LON, 15.0 if kind == "AC" else 200.0, 20 + i, 9 + i, 300 - i))
            t, i = t + dt.timedelta(minutes=5), i + 1
        c.commit()
    return charge


def _section():
    text = diagnostics.build_bundle("9.9.9", parts=("info",))
    return text.split("----- charge readings (the latest charge charts) -----\n", 1)[1].split("\n\n-----", 1)[0]


def _headers(section):
    return [l.split()[0] for l in section.splitlines() if l.startswith("  #")]


def test_a_charge_reads_as_its_chart_draws_it(install):
    install(1, T0)
    curve = db_reader.get_charge_power_curve(1)
    rows = [l.split() for l in _section().splitlines() if l.startswith("    21:")]
    assert len(rows) == len(curve["times"]) == 5
    assert rows[0] == ["21:00:00", "20.0", "6.00", "9", "4.5", "300"]
    assert [float(r[2]) for r in rows] == curve["power"]
    assert [int(r[3]) for r in rows] == curve["battery_temp"]


def test_the_last_ten_of_each_kind_however_old(install):
    install(100, T0 - dt.timedelta(days=60), kind="DC")          # the one road trip, two months back
    for k in range(12):                                           # and a home charge every night since
        install(k + 1, T0 + dt.timedelta(days=k))
    assert _headers(_section()) == [f"#{k}" for k in range(12, 2, -1)] + ["#100"]


def test_a_merged_charge_is_one_session_with_all_its_readings(install):
    install(1, T0)
    install(2, T0 + dt.timedelta(minutes=25), merged_into=1)
    section = _section()
    assert [l for l in section.splitlines() if l.startswith("  #")] == [
        "  #1 AC/HOME 2026-11-01T21:00 → 2026-11-01T21:45 · 10 readings"]


def test_a_merged_charge_is_the_kind_of_its_largest_piece(install):
    """The Charges page reads a session by the piece that carried the most energy; so does the bundle."""
    install(100, T0 - dt.timedelta(days=60), kwh=1.0)                                   # AC to the car
    install(101, T0 - dt.timedelta(days=60, minutes=-25), kind="DC", merged_into=100, kwh=40.0)
    for k in range(10):
        install(k + 1, T0 + dt.timedelta(days=k))
    assert _headers(_section())[-1] == "#100"
    assert "  #100 DC/HOME" in _section()


def test_a_charge_with_no_recorded_type_is_dc_above_11_kw(install):
    """As the Charges page tags it; a recorded AC stays AC at any power."""
    install(100, T0 - dt.timedelta(days=60), kind=None, peak=80.0)
    install(101, T0 - dt.timedelta(days=50), peak=80.0)
    for k in range(10):
        install(k + 1, T0 + dt.timedelta(days=k))
    assert _headers(_section()) == [f"#{k}" for k in range(10, 0, -1)] + ["#100"]


def test_a_charge_in_progress_reads_by_its_peak_so_far(install):
    """Its row holds the schema's default type, AC, until the poller types it when it ends."""
    for k in range(10):
        install(k + 1, T0 + dt.timedelta(days=k))
    install(100, T0 + dt.timedelta(days=20), peak=80.0, ended=False)
    assert _headers(_section()) == [f"#{k}" for k in range(10, 0, -1)] + ["#100"]


def test_nothing_says_where_the_car_charged(install):
    install(1, T0)
    install(2, T0 + dt.timedelta(days=1), kind="DC")
    section = _section()
    assert str(LAT)[:5] not in section and str(LON)[:4] not in section
    assert VIN not in section and VIN[-4:] not in section


def test_no_charges_says_so(install):
    assert _section() == "(no charges)"
