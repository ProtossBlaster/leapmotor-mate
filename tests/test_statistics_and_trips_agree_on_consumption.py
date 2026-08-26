"""The Statistics page and the Trips page must print the SAME kWh/100km for the same month — beta
#35 (@michapr, on his real July): Trips read 10.1 (getEC over the km getEC covers), Statistics read
12.2 (its own column `efficiency_kwh_100km` over the km THAT has), 36 trips against 34, a UTC month
against a local one. Three ways of saying one number.

get_stats_grouped is rebuilt on the SAME `_localized_trips` + `_totals_*` machinery the Trips
calendar already uses, so the two cannot disagree by construction: getEC ÷ km-with-getEC, non-merged
trips, local month.
"""
import pytest

pytest.importorskip("fastapi", reason="db_reader pulls web deps not in the minimal CI env")


def _env(tmp_path, monkeypatch):
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    # Two July trips where the stored efficiency (20) and getEC disagree: each drove 100 km on 10 kWh
    # of getEC (= 10 kWh/100km) but carries efficiency 20. Statistics used to read 20; it must read
    # the getEC 10, exactly what the Trips page reads.
    pdb._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, ec_kwh,"
                      " efficiency_kwh_100km) VALUES "
                      "(1,1,'2026-07-10T08:00:00+00:00','2026-07-10T09:00:00+00:00',100,10.0,20.0)")
    pdb._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, ec_kwh,"
                      " efficiency_kwh_100km) VALUES "
                      "(2,1,'2026-07-11T08:00:00+00:00','2026-07-11T09:00:00+00:00',100,10.0,20.0)")
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def _july(grouped):
    for y in grouped:
        m = (y.get("months") or {}).get("2026-07")
        if m:
            return m
    return None


def test_statistics_month_reports_the_getec_figure(tmp_path, monkeypatch):
    db_reader = _env(tmp_path, monkeypatch)
    jul = _july(db_reader.get_stats_grouped())
    assert jul is not None, "July missing from the grouped stats"
    assert jul["avg_efficiency"] == 10.0, f"still on efficiency_kwh_100km, not getEC: {jul['avg_efficiency']}"


def test_statistics_and_trips_agree(tmp_path, monkeypatch):
    db_reader = _env(tmp_path, monkeypatch)
    stats = _july(db_reader.get_stats_grouped())["avg_efficiency"]
    trips = db_reader.get_trips_calendar_month(2026, 7)["total"]["kwh_100km"]
    assert stats == trips == 10.0, f"stats {stats} vs trips {trips}"


def test_a_merged_child_is_not_counted(tmp_path, monkeypatch):
    """The Trips page builds on get_trips, which drops merged children; Statistics must too, or its
    trip_count runs ahead (michapr: 36 vs 34)."""
    import db as D
    import db_reader
    db_reader = _env(tmp_path, monkeypatch)
    pdb = D.Database(db_reader.DB_PATH)
    pdb._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, ec_kwh,"
                      " efficiency_kwh_100km, merged_into_id) VALUES "
                      "(3,1,'2026-07-12T08:00:00+00:00','2026-07-12T09:00:00+00:00',50,5.0,10.0,1)")
    pdb._conn.commit()
    pdb._conn.close()
    assert _july(db_reader.get_stats_grouped())["trip_count"] == 2, "the merged child was counted"
