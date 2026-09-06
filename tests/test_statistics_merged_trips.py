"""Beta #44: statistics must pair a group's energy with the whole group's distance."""
from datetime import datetime

import pytest

import db as D
import db_reader as reader


def window(day="2026-07-21"):
    start = int(datetime.fromisoformat(day + "T00:00:00+00:00").timestamp())
    return start, start + 86399


@pytest.fixture
def trips(tmp_path, monkeypatch):
    database = D.Database(str(tmp_path / "stats.db"))
    con = database._conn
    monkeypatch.setattr(reader, "_get", lambda: con)
    monkeypatch.setattr(reader, "_conn_rw", lambda: con)
    monkeypatch.setattr(reader, "_current_vehicle_id", lambda: 1)
    monkeypatch.setattr(reader, "is_reev_car", lambda: True)
    monkeypatch.setattr(reader, "get_battery_capacity_kwh", lambda: 50)
    con.executemany(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, "
        "efficiency_kwh_100km, ec_kwh, ec_stable, merged_into_id, regen_kwh) "
        "VALUES (?,1,?,?,?,?,?,?,?,?,?)",
        [(14, "2026-07-21T07:14:41+00:00", "2026-07-21T07:18:00+00:00",
          1, 3, 5.7, .4, 1, None, .01),
         (15, "2026-07-21T07:19:33+00:00", "2026-07-21T07:30:00+00:00",
          6, 10, None, None, 0, 14, .02)],
    )
    con.commit()
    yield con
    con.close()


@pytest.mark.parametrize("key,want", [
    ("trip_count", 1), ("distance_km", 7), ("duration_min", 13),
    ("ec_km", 7), ("eff_km", 7), ("measured_eff_km", 7),
    ("energy_kwh", .4), ("measured_energy_kwh", .4),
])
def test_period_counts_combined_distance_and_energy(trips, key, want):
    assert reader.get_trip_totals_between(*window())[key] == want


@pytest.mark.parametrize("key,want", [
    ("trip_count", 1), ("total_km", 7), ("total_kwh_used", .4),
    ("energy_trips", 1), ("energy_km", 7), ("avg_efficiency_km", 7),
    ("avg_efficiency", 5.7), ("total_drive_min", 13),
    ("total_regen_kwh", .03), ("avg_regen_kwh", .03),
])
def test_summary_counts_one_logical_trip(trips, key, want):
    assert reader.get_stats_summary()[key] == want


def test_group_is_anchored_to_parent_day_even_when_child_crosses_midnight(trips):
    trips.execute("UPDATE trips SET started_at='2026-07-22T00:01:00+00:00', "
                  "ended_at='2026-07-22T00:10:00+00:00' WHERE id=15")
    assert reader.get_trip_totals_between(*window())["ec_km"] == 7
    following = reader.get_trip_totals_between(*window("2026-07-22"))
    assert following["trip_count"] == 0
    assert following["distance_km"] is None


def test_other_vehicle_and_open_trips_are_excluded(trips):
    trips.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, "
                  "ec_kwh, efficiency_kwh_100km) VALUES "
                  "(16,2,'2026-07-21T08:00:00+00:00','2026-07-21T09:00:00+00:00',100,20,20)")
    trips.execute("INSERT INTO trips (id, vehicle_id, started_at, distance_km) VALUES "
                  "(17,1,'2026-07-21T10:00:00+00:00',200)")
    assert reader.get_trip_totals_between(*window())["trip_count"] == 1
    assert reader.get_stats_summary()["total_km"] == 7


def test_generator_group_keeps_cloud_energy_but_not_battery_only_average(trips):
    trips.execute("UPDATE trips SET fuel_start_pct=80, fuel_end_pct=80 WHERE id=14")
    trips.execute("UPDATE trips SET fuel_start_pct=80, fuel_end_pct=75 WHERE id=15")
    period = reader.get_trip_totals_between(*window())
    summary = reader.get_stats_summary()
    assert period["ec_km"] == 7
    assert period["measured_eff_km"] is None
    assert period["energy_kwh"] == 0
    assert summary["total_kwh_used"] == .4
    assert summary["avg_efficiency"] is None


@pytest.mark.parametrize("reev,want_km", [(True, None), (False, 7)])
def test_estimated_group_is_measured_only_when_cloud_has_data(trips, monkeypatch, reev, want_km):
    monkeypatch.setattr(reader, "is_reev_car", lambda: reev)
    trips.execute("UPDATE trips SET ec_kwh=NULL, ec_stable=0, start_soc=80, end_soc=79 WHERE id=14")
    trips.execute("UPDATE trips SET start_soc=79, end_soc=78 WHERE id=15")
    period = reader.get_trip_totals_between(*window())
    assert period["eff_km"] == 7
    assert period["measured_eff_km"] is None
    assert period["energy_kwh"] == 1
    assert reader.get_stats_summary()["avg_efficiency_km"] == want_km


def test_reading_statistics_never_changes_stored_segments(trips):
    before = [tuple(r) for r in trips.execute("SELECT * FROM trips ORDER BY id")]
    reader.get_trip_totals_between(*window())
    reader.get_stats_summary()
    assert [tuple(r) for r in trips.execute("SELECT * FROM trips ORDER BY id")] == before


def test_unmerge_restores_separate_trip_statistics(trips):
    trips.execute("UPDATE trips SET efficiency_soc=10 WHERE id=14")
    assert reader.get_stats_summary()["trip_count"] == 1
    assert reader.unmerge_trip(14)["ok"]
    period = reader.get_trip_totals_between(*window())
    assert period["trip_count"] == 2
    assert period["distance_km"] == 7
    assert period["energy_kwh"] == .1
    assert period["ec_km"] == 0


def test_odometer_group_distance_matches_trip_list(trips):
    trips.execute("UPDATE trips SET start_odometer_km=100, end_odometer_km=101 WHERE id=14")
    trips.execute("UPDATE trips SET start_odometer_km=101, end_odometer_km=108 WHERE id=15")
    assert reader.get_trip_totals_between(*window())["ec_km"] == 8
    assert reader.get_stats_summary()["total_km"] == 8


def test_child_cloud_values_are_not_counted_twice(trips):
    trips.execute("UPDATE trips SET ec_kwh=.3, ec_stable=1, efficiency_kwh_100km=5 WHERE id=15")
    assert reader.get_trip_totals_between(*window())["energy_kwh"] == .4
    assert reader.get_stats_summary()["total_kwh_used"] == .4


def test_reconstructed_segment_does_not_inflate_driving_time(trips):
    trips.execute("UPDATE trips SET reconstructed=1, duration_min=600 WHERE id=15")
    summary = reader.get_stats_summary()
    assert summary["trip_count"] == 1
    assert summary["total_drive_min"] == 3
    assert summary["drive_time_excluded"] == 1


def test_group_without_energy_stays_unknown_not_measured_zero(trips):
    trips.execute("UPDATE trips SET ec_kwh=NULL, ec_stable=0, efficiency_kwh_100km=NULL")
    period = reader.get_trip_totals_between(*window())
    summary = reader.get_stats_summary()
    assert period["trip_count"] == 1
    assert period["distance_km"] == 7
    assert period["ec_km"] == 0
    assert period["measured_eff_km"] is None
    assert summary["total_kwh_used"] is None
    assert summary["energy_km"] is None


def test_mixed_groups_and_standalone_trips_use_distance_weighted_average(trips, monkeypatch):
    trips.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, "
                  "efficiency_kwh_100km, ec_kwh, ec_stable, regen_kwh) VALUES "
                  "(16,1,'2026-07-21T08:00:00+00:00','2026-07-21T09:00:00+00:00',100,20,20,1,1)")
    trips.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, "
                  "efficiency_kwh_100km) VALUES "
                  "(17,1,'2026-07-21T10:00:00+00:00','2026-07-21T11:00:00+00:00',10,30)")
    period = reader.get_trip_totals_between(*window())
    assert period["trip_count"] == 3
    assert period["distance_km"] == 117
    assert period["energy_kwh"] == 23.4
    assert period["measured_energy_kwh"] == 20.4
    assert period["measured_eff_km"] == 107
    summary = reader.get_stats_summary()
    assert summary["total_kwh_used"] == 23.4
    assert summary["avg_efficiency"] == 19.1
    assert summary["avg_efficiency_km"] == 107
    monkeypatch.setattr(reader, "is_reev_car", lambda: False)
    assert reader.get_stats_summary()["avg_efficiency"] == 20
    assert reader.get_stats_summary()["avg_efficiency_km"] == 117


def test_unrelated_period_keeps_sql_rounding_when_merges_exist_elsewhere(trips):
    trips.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, "
                  "efficiency_kwh_100km) VALUES "
                  "(16,1,'2026-07-22T08:00:00+00:00','2026-07-22T09:00:00+00:00',1,12.5)")
    # SQLite rounds 0.125 to 0.13, not Python's ties-to-even 0.12.
    assert reader.get_trip_totals_between(*window("2026-07-22"))["energy_kwh"] == .13


def test_missing_distance_is_not_changed_into_zero_energy(trips):
    trips.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at) VALUES "
                  "(16,1,'2026-07-22T08:00:00+00:00','2026-07-22T09:00:00+00:00')")
    period = reader.get_trip_totals_between(*window("2026-07-22"))
    assert period["trip_count"] == 1
    assert period["energy_kwh"] is None
