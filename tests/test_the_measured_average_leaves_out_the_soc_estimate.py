"""beta #43 (@michapr): the battery-only average called itself measured and wasn't.

`efficiency_kwh_100km` is written at trip end from ΔSoC × capacity by the poller, and only
overwritten with the car's getEC figure once the cloud answers — so a trip the cloud never covered
carries an ESTIMATE in the same column. Averaging over "every trip that has an efficiency"
therefore mixed the two, on the one card whose wording promises it did not.

Measured on his own bundle (31/08, B10 REEV, 88 trips): 4 trips and 5 km of 625 came from ΔSoC,
moving the average from 14.30 to 14.26 kWh/100 km — and those four are 1-2 km hops reading 35.72
and 18.8, which is what a battery percentage does over one kilometre.
"""
from datetime import datetime, timezone

import pytest


def _seed(tmp_path, monkeypatch):
    import db as D
    import db_reader

    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    ts = "2026-08-01T12:00:00+00:00"
    #    id  km     eff    ec_kwh   → what it is
    for tid, dist, eff, ec in (
        (1, 100.0, 20.0, 20.0),     # cloud measured it
        (2,  50.0, 10.0,  5.0),     # cloud measured it
        (3,   1.0, 35.7, None),     # ΔSoC over one kilometre — the kind beta #43 is about
        (4,  25.0, None, 12.0),     # generator trip: Mate blanks the efficiency
    ):
        pdb._conn.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
            " start_soc, end_soc, efficiency_kwh_100km, ec_kwh, regen_kwh, duration_min)"
            " VALUES (?,1,?,?,?,60,50,?,?,0,30)", (tid, ts, ts, dist, eff, ec))
    pdb._conn.commit()
    return db_reader


def _window():
    return (int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()),
            int(datetime(2026, 8, 2, tzinfo=timezone.utc).timestamp()))


def test_the_measured_pair_drops_the_estimated_trip(tmp_path, monkeypatch):
    db_reader = _seed(tmp_path, monkeypatch)
    tot = db_reader.get_trip_totals_between(*_window())
    # 100 + 50 + 1 = 151 km carry an efficiency; only 150 of them were measured by the cloud.
    assert tot["eff_km"] == 151.0
    assert tot["measured_eff_km"] == 150.0
    # 20 + 5 + 0.357 = 25.357 against 25.0
    assert tot["energy_kwh"] == 25.36
    assert tot["measured_energy_kwh"] == 25.0


def test_the_local_reference_for_a_short_cloud_total_still_counts_everything(tmp_path, monkeypatch):
    """`energy_kwh` feeds flag_short_cloud_total (#212 @riri19). A reference that ignored the trips
    the cloud missed would be the wrong yardstick for judging what the cloud reported — this is
    why the measured pair is a SEPARATE pair and not a narrowing of that one."""
    db_reader = _seed(tmp_path, monkeypatch)
    tot = db_reader.get_trip_totals_between(*_window())
    assert tot["energy_kwh"] > tot["measured_energy_kwh"]


def test_the_statistics_average_is_measured_only_on_a_range_extender(tmp_path, monkeypatch):
    db_reader = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(db_reader, "is_reev_car", lambda: True)
    reev = db_reader.get_stats_summary()
    monkeypatch.setattr(db_reader, "is_reev_car", lambda: False)
    bev = db_reader.get_stats_summary()

    # REEV: the 1 km ΔSoC hop is out — (100*20 + 50*10) / 150
    assert reev["avg_efficiency_km"] == 150.0
    assert reev["avg_efficiency"] == pytest.approx(16.7, abs=0.05)
    # A full-electric car promises nothing about the source and keeps its reach.
    assert bev["avg_efficiency_km"] == 151.0
