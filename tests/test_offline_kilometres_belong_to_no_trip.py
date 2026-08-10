"""Kilometres driven while the cloud was silent belong to NO trip — and Mate says so.

Silvio, 10/08/2026, after tracing #244 end to end:

    «non potendo identificare a chi appartengono i KM in maniera netta ed univoca, bisogna
     escluderli da tutto e far in modo di comunicarlo»

WHAT WAS HAPPENING. `_offline_head` moved a new trip's start anchors BACK over kilometres nobody
watched, so the trip was born already long. Put in D after a silent stretch and the trip opens
carrying 50 km, the SoC drop that went with them, and a start position 50 km away — before the car
has moved a metre. Those kilometres may belong to the drive that is starting, to the drive that
ended hours ago, or to two drives with a night's parking in between. Nothing in the data says which.

WHAT HAPPENS NOW. The trip starts where the signal returned — odometer, SoC and position all live.
The unattributed stretch is written to its own row: when it began (the last moment the cloud had
news), when it ended, how far, how much charge. Excluding it from distances, consumption and costs
needs no subtraction anywhere: it is simply never put in.

⚠️ BOTH HALVES OF THE FRACTION, ALWAYS TOGETHER. Dropping the kilometres while keeping the SoC drop
would make every consumption figure WORSE, not better — whole energy over partial distance. That is
the shape of the SoH defect (v3.10.2) and of #237, and it is the one thing here that must not slip.

⛔ This supersedes #130 and #233, both of which @riri19 asked for. The kilometres are not lost —
they move to a card that names them — but his trips start where we found them again. Silvio's call,
pending: this file is the trial build.

CI-safe: pure recorder / db logic, no fastapi.
"""
from client import VehicleData

import db as D
import recorder as R


def _vd(*, odo, soc, gear="P", speed=0.0, lat=45.0, lon=9.0, ts_ms=0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts_ms, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="parked",
        charging_status=0, charge_power_kw=0.0, latitude=lat, longitude=lon,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


def _rec(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(65.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    return db, R.Recorder(db, vehicle_id=vid)


def _silence_then_drive(rec, *, frozen_odo, frozen_soc, resume_odo, resume_soc,
                        end_odo, end_soc, frozen_polls=4):
    """Park, let the cloud repeat one frame, then have the signal return with the car ALREADY
    driving and the odometer advanced — the shape that welded a whole day together."""
    rec.process(_vd(odo=frozen_odo, soc=frozen_soc, ts_ms=1000))
    for _ in range(frozen_polls):
        rec.process(_vd(odo=frozen_odo, soc=frozen_soc, ts_ms=1000))   # same frame id = a repeat
    rec.process(_vd(odo=resume_odo, soc=resume_soc, gear="D", speed=50.0, ts_ms=2000))
    rec.process(_vd(odo=end_odo, soc=end_soc, gear="D", speed=50.0, ts_ms=3000))
    for _ in range(6):
        rec.process(_vd(odo=end_odo, soc=end_soc, ts_ms=4000))


def _trip(db):
    return db._conn.execute("SELECT * FROM trips ORDER BY id DESC LIMIT 1").fetchone()


def _gaps(db):
    return db._conn.execute("SELECT * FROM offline_gaps ORDER BY id").fetchall()


# ── the trip keeps only what Mate watched ──────────────────────────────────────
def test_the_trip_starts_where_the_signal_returned(tmp_path):
    """50 km appeared while the cloud was quiet. The trip that opens must not claim them."""
    db, rec = _rec(tmp_path)
    _silence_then_drive(rec, frozen_odo=1000, frozen_soc=80.0,
                        resume_odo=1050, resume_soc=70.0, end_odo=1060, end_soc=68.0)
    t = _trip(db)
    assert t["start_odometer_km"] == 1050
    assert t["distance_km"] == 10.0, "the trip is only the part Mate saw"


def test_the_energy_leaves_with_the_kilometres(tmp_path):
    """The half that must never be forgotten: the SoC anchor goes too. Keeping it would divide
    whole energy by partial distance — worse than the defect being fixed."""
    db, rec = _rec(tmp_path)
    _silence_then_drive(rec, frozen_odo=1000, frozen_soc=80.0,
                        resume_odo=1050, resume_soc=70.0, end_odo=1060, end_soc=68.0)
    assert _trip(db)["start_soc"] == 70.0


def test_the_trip_starts_where_the_car_actually_is(tmp_path):
    """Position follows the same rule. Drawing a start 50 km away for a trip whose distance begins
    here would put a line on the map that matches no kilometre."""
    db, rec = _rec(tmp_path)
    db, rec = _rec(tmp_path)
    rec.process(_vd(odo=1000, soc=80.0, lat=45.0, lon=9.0, ts_ms=1000))
    rec.process(_vd(odo=1000, soc=80.0, lat=45.0, lon=9.0, ts_ms=1000))
    rec.process(_vd(odo=1050, soc=70.0, gear="D", speed=50.0, lat=46.0, lon=10.0, ts_ms=2000))
    t = _trip(db)
    assert (t["start_lat"], t["start_lon"]) == (46.0, 10.0)


# ── and the stretch nobody watched gets a row of its own ───────────────────────
def test_the_unwatched_stretch_is_recorded(tmp_path):
    db, rec = _rec(tmp_path)
    _silence_then_drive(rec, frozen_odo=1000, frozen_soc=80.0,
                        resume_odo=1050, resume_soc=70.0, end_odo=1060, end_soc=68.0)
    gaps = _gaps(db)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["distance_km"] == 50.0
    assert g["soc_start"] == 80.0 and g["soc_end"] == 70.0
    assert round(g["energy_kwh"], 2) == round(10.0 / 100 * 65.0, 2)   # ΔSoC × capacity


def test_the_window_runs_from_the_last_news_to_the_first(tmp_path, monkeypatch):
    """Not 'the last poll': the cloud repeating a frame is not news. `_last_fresh_ts` (v3.10.5) is
    the moment it last told us something, and that is where the silence began.

    ⚠️ This needs a CLOCK. Without one the polls happen inside the same millisecond of real time,
    the two candidate timestamps come out identical, and the test passes whichever is used — which
    is exactly what a mutation proved before this fixture existed."""
    clock = {"now": "2026-08-10T18:00:00+00:00"}
    monkeypatch.setattr(D, "_now_iso", lambda: clock["now"])
    monkeypatch.setattr(R, "_now_iso", lambda: clock["now"])

    db, rec = _rec(tmp_path)
    rec.process(_vd(odo=1000, soc=80.0, ts_ms=1000))          # the cloud's last news, 18:00
    for minute in range(1, 40):                               # …then 39 minutes of repeats
        clock["now"] = f"2026-08-10T18:{minute:02d}:00+00:00"
        rec.process(_vd(odo=1000, soc=80.0, ts_ms=1000))
    clock["now"] = "2026-08-10T18:40:00+00:00"
    rec.process(_vd(odo=1050, soc=70.0, gear="D", speed=50.0, ts_ms=2000))

    g = _gaps(db)[0]
    assert g["started_at"] == "2026-08-10T18:00:00+00:00", "the silence began at the last NEWS"
    assert g["ended_at"] == "2026-08-10T18:40:00+00:00"
    assert g["odometer_start"] == 1000 and g["odometer_end"] == 1050


# ── and nothing at all on a healthy link ───────────────────────────────────────
def test_a_healthy_link_records_no_gap(tmp_path):
    db, rec = _rec(tmp_path)
    rec.process(_vd(odo=1000, soc=80.0, ts_ms=1000))
    rec.process(_vd(odo=1000, soc=80.0, gear="D", speed=30.0, ts_ms=2000))
    rec.process(_vd(odo=1010, soc=78.0, gear="D", speed=50.0, ts_ms=3000))
    assert _gaps(db) == []
    assert _trip(db)["start_odometer_km"] == 1000


def test_a_jump_too_small_to_be_real_is_not_a_gap(tmp_path):
    """The odometer reads in whole kilometres; below one, a jump cannot be told from quantisation."""
    db, rec = _rec(tmp_path)
    rec.process(_vd(odo=1000, soc=80.0, ts_ms=1000))
    rec.process(_vd(odo=1000, soc=80.0, ts_ms=1000))
    rec.process(_vd(odo=1000, soc=79.9, gear="D", speed=30.0, ts_ms=2000))
    assert _gaps(db) == []


def test_a_zero_odometer_never_becomes_a_gap(tmp_path):
    """A partial frame reading 0 would otherwise book the car's entire lifetime mileage."""
    db, rec = _rec(tmp_path)
    rec.process(_vd(odo=0, soc=80.0, ts_ms=1000))
    rec.process(_vd(odo=0, soc=80.0, ts_ms=1000))
    rec.process(_vd(odo=1050, soc=70.0, gear="D", speed=50.0, ts_ms=2000))
    assert _gaps(db) == []


def test_the_card_totals_what_could_not_be_attributed(tmp_path, monkeypatch):
    """What the Statistics card reports: four numbers over every recorded gap."""
    import db_reader
    db, rec = _rec(tmp_path)
    for odo0, odo1, soc0, soc1 in ((1000, 1050, 80.0, 70.0), (2000, 2024, 60.0, 55.0)):
        db.record_offline_gap(1, started_at="2026-08-09T10:00:00+00:00",
                              ended_at="2026-08-09T12:00:00+00:00",
                              odo_start=odo0, odo_end=odo1, soc_start=soc0, soc_end=soc1)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))

    s = db_reader.offline_gaps_summary()
    assert s["count"] == 2
    assert s["total_km"] == 74.0
    assert s["total_soc"] == 15.0
    assert round(s["total_kwh"], 2) == round(15.0 / 100 * 65.0, 2)


def test_a_window_that_ended_higher_never_refunds_charge(tmp_path, monkeypatch):
    """A gap that ends with MORE charge than it started held a charging session. Summing the signed
    difference would subtract it from the other windows and quietly refund energy the car never
    spent on the road — the card would under-report its own total."""
    import db_reader
    db, _ = _rec(tmp_path)
    db.record_offline_gap(1, started_at="2026-08-09T10:00:00+00:00",
                          ended_at="2026-08-09T12:00:00+00:00",
                          odo_start=1000, odo_end=1050, soc_start=80.0, soc_end=70.0)
    db.record_offline_gap(1, started_at="2026-08-09T20:00:00+00:00",
                          ended_at="2026-08-09T23:00:00+00:00",
                          odo_start=2000, odo_end=2010, soc_start=40.0, soc_end=90.0)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))

    s = db_reader.offline_gaps_summary()
    assert s["total_km"] == 60.0, "both stretches really happened"
    assert s["total_soc"] == 10.0, "the charging one contributes nothing, never a negative"


def test_the_euro_is_omitted_when_no_charge_carries_a_price(tmp_path, monkeypatch):
    """Mate's average €/kWh divides over PRICED charges alone and is None when there are none.
    A card that then printed 0,00 € would be inventing a free kilometre."""
    import db_reader
    db, rec = _rec(tmp_path)
    db.record_offline_gap(1, started_at="2026-08-09T10:00:00+00:00",
                          ended_at="2026-08-09T12:00:00+00:00",
                          odo_start=1000, odo_end=1050, soc_start=80.0, soc_end=70.0)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))

    assert db_reader.offline_gaps_summary()["cost"] is None


def test_the_trips_page_asks_for_one_month_only(tmp_path, monkeypatch):
    """The Statistics card is the running total; the Viaggi calendar wants the month it is
    showing, and nothing else."""
    import db_reader
    db, _ = _rec(tmp_path)
    db.record_offline_gap(1, started_at="2026-07-15T10:00:00+00:00",
                          ended_at="2026-07-15T12:00:00+00:00",
                          odo_start=1000, odo_end=1010, soc_start=80.0, soc_end=78.0)
    db.record_offline_gap(1, started_at="2026-08-15T10:00:00+00:00",
                          ended_at="2026-08-15T12:00:00+00:00",
                          odo_start=2000, odo_end=2050, soc_start=80.0, soc_end=70.0)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))

    assert db_reader.offline_gaps_summary(2026, 8)["total_km"] == 50.0
    assert db_reader.offline_gaps_summary(2026, 7)["total_km"] == 10.0
    assert db_reader.offline_gaps_summary(2026, 9)["count"] == 0
    assert db_reader.offline_gaps_summary()["total_km"] == 60.0, "no month = everything"


def test_the_month_is_the_one_on_the_clock_at_home(tmp_path, monkeypatch):
    """23:30 UTC on 31 July is 01:30 on 1 August in Rome, and the calendar puts it in August. A
    figure beside that calendar that filtered on UTC would disagree with the grid it sits under —
    the timestamps in the database are ALWAYS UTC and the display is ALWAYS local."""
    import db_reader
    db, _ = _rec(tmp_path)
    db._conn.execute("INSERT INTO settings (key, value) VALUES ('timezone','Europe/Rome')")
    db._conn.commit()
    db.record_offline_gap(1, started_at="2026-07-31T23:30:00+00:00",
                          ended_at="2026-08-01T00:10:00+00:00",
                          odo_start=1000, odo_end=1042, soc_start=80.0, soc_end=72.0)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))

    assert db_reader.offline_gaps_summary(2026, 8)["total_km"] == 42.0
    assert db_reader.offline_gaps_summary(2026, 7)["count"] == 0


def test_the_card_stays_away_when_there_is_nothing_to_declare(tmp_path, monkeypatch):
    import db_reader
    _rec(tmp_path)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    assert db_reader.offline_gaps_summary()["count"] == 0


def test_a_gap_where_the_charge_rose_is_not_a_drive(tmp_path):
    """SoC up over the silence means a charge happened in there. The kilometres are still real, so
    the row is still written — but the energy must not go negative and pretend the car gained it
    back by driving."""
    db, rec = _rec(tmp_path)
    _silence_then_drive(rec, frozen_odo=1000, frozen_soc=40.0,
                        resume_odo=1050, resume_soc=90.0, end_odo=1060, end_soc=88.0)
    g = _gaps(db)[0]
    assert g["distance_km"] == 50.0
    assert g["energy_kwh"] == 0.0, "a rise is a charge, not consumption"
