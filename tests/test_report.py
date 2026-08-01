"""Monthly Report (get_monthly_report): one month of driving + charging + cost, with deltas
vs the previous calendar month and gap-skipping ◀ ▶ navigation.

The € must match the Charges page exactly (frozen per-row charges.cost summed) and the energy
must use the billed basis (_billed_kwh: wallbox AC for HOME, battery DC otherwise). Runs on a
tmp_path DB (poller schema + db_reader pointed at it) — CI-safe. Timestamps sit mid-month and
midday so local-time bucketing lands in the intended month under any test-runner timezone."""
import db as D            # poller schema (creates trips/charges tables + migrations)
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(65.0)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _trip(pdb, tid, *, month="2026-05", dist=100.0, eff=20.0, regen=0.0, dur=60.0):
    ts = f"{month}-15T12:00:00+00:00"
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " start_soc, end_soc, efficiency_kwh_100km, regen_kwh, duration_min)"
        " VALUES (?,1,?,?,?,60,50,?,?,?)",
        (tid, ts, ts, dist, eff, regen, dur))
    pdb._conn.commit()


def _charge(pdb, cid, *, month="2026-05", location_type, energy_added, cost, ac=None):
    ts = f"{month}-15T12:00:00+00:00"
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type, cost)"
        " VALUES (?,1,?,?,40,60,?,?,?,?)",
        (cid, ts, ts, energy_added, ac, location_type, cost))
    pdb._conn.commit()


def _pos(pdb, tid, pts):
    for i, (lat, lon) in enumerate(pts):
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude)"
            " VALUES (?,?,?,?)",
            (tid, f"2026-05-15T12:{i:02d}:00+00:00", lat, lon))
    pdb._conn.commit()


def test_no_data(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = db_reader.get_monthly_report()
    assert r["has_data"] is False
    assert r["months"] == []


def test_single_month_aggregates(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _trip(pdb, 1, month="2026-05", dist=100.0, eff=20.0, regen=1.5)
    _trip(pdb, 2, month="2026-05", dist=50.0,  eff=16.0, regen=0.5)
    # HOME billed on the wallbox AC (10 kWh), public DC billed on battery (20 kWh)
    _charge(pdb, 1, month="2026-05", location_type="HOME", energy_added=8.0,  ac=10.0, cost=2.5)
    _charge(pdb, 2, month="2026-05", location_type="DC",   energy_added=20.0, ac=None, cost=10.0)

    r = db_reader.get_monthly_report("2026-05")
    c = r["cur"]
    assert r["has_data"] and r["month"] == "2026-05"
    # driving
    assert c["trip_count"] == 2
    assert c["total_km"] == 150.0
    assert c["total_kwh_used"] == 28.0                 # 100*0.20 + 50*0.16
    assert c["avg_efficiency"] == 18.7                 # (2000+800)/150, distance-weighted
    assert c["regen_kwh"] == 2.0
    # charging + cost
    assert c["charge_count"] == 2
    assert c["charge_kwh"] == 30.0                     # 10 (AC) + 20 (DC battery)
    assert c["charge_cost"] == 12.5
    assert r["avg_price"] == 0.417                     # 12.5 / 30
    # home vs public split (billed basis)
    assert c["home"]   == {"count": 1, "kwh": 10.0, "cost": 2.5}
    assert c["public"] == {"count": 1, "kwh": 20.0, "cost": 10.0}
    assert c["unconfirmed"] == 0


def test_default_and_invalid_month(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _trip(pdb, 1, month="2026-04")
    _trip(pdb, 2, month="2026-06")
    assert db_reader.get_monthly_report()["month"] == "2026-06"          # None → latest
    assert db_reader.get_monthly_report("1999-01")["month"] == "2026-06"  # unknown → latest
    assert [m["key"] for m in db_reader.get_monthly_report()["months"]] == ["2026-06", "2026-04"]


def test_prev_month_deltas(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _trip(pdb, 1, month="2026-05", dist=150.0, eff=20.0)
    _charge(pdb, 1, month="2026-05", location_type="HOME", energy_added=8.0, ac=10.0, cost=10.0)
    _trip(pdb, 2, month="2026-06", dist=300.0, eff=22.0)
    _charge(pdb, 2, month="2026-06", location_type="HOME", energy_added=8.0, ac=10.0, cost=5.0)

    r = db_reader.get_monthly_report("2026-06")
    assert r["deltas"] is not None
    assert r["deltas"]["km"]["pct"] == 100             # 150 → 300
    assert r["deltas"]["cost"]["diff"] == -5.0         # 10 → 5
    assert r["deltas"]["cost"]["pct"] == -50
    # May is the earliest month → no prior calendar month → no deltas
    assert db_reader.get_monthly_report("2026-05")["deltas"] is None


def test_navigation_skips_gaps(tmp_path, monkeypatch):
    """◀ ▶ jump to the nearest month WITH data (April is empty and skipped), while the
    comparison deltas use the immediately-preceding CALENDAR month (empty → None)."""
    pdb = _setup(tmp_path, monkeypatch)
    _trip(pdb, 1, month="2026-03")
    _trip(pdb, 2, month="2026-05")
    _trip(pdb, 3, month="2026-06")

    r = db_reader.get_monthly_report("2026-05")
    assert r["prev_month"] == "2026-03"                # skips empty April
    assert r["next_month"] == "2026-06"
    assert r["deltas"] is None                         # calendar-prev April has no data

    top = db_reader.get_monthly_report("2026-06")
    assert top["next_month"] is None                   # newest
    assert top["prev_month"] == "2026-05"


def test_unconfirmed_excluded_from_split(tmp_path, monkeypatch):
    """An untyped charge counts in the headline totals but stays out of the home/public split."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, month="2026-05", location_type="HOME", energy_added=8.0, ac=10.0, cost=2.5)
    _charge(pdb, 2, month="2026-05", location_type=None,   energy_added=12.0, ac=None, cost=None)

    c = db_reader.get_monthly_report("2026-05")["cur"]
    assert c["charge_count"] == 2
    assert c["charge_kwh"] == 22.0                     # 10 + 12, both in the total
    assert c["charge_cost"] == 2.5                     # only the priced one
    assert c["unconfirmed"] == 1
    assert c["home"]["count"] == 1 and c["public"]["count"] == 0


def test_month_track_filters_by_month(tmp_path, monkeypatch):
    """The month map shows only trips STARTED in the selected local month; each trip is its
    own track (one solid run — the fixture's points are 1 minute apart, well under the gap
    threshold), endpoints preserved, empty/bogus month → []."""
    pdb = _setup(tmp_path, monkeypatch)
    _trip(pdb, 1, month="2026-05")
    _trip(pdb, 2, month="2026-06")
    _pos(pdb, 1, [(45.40, 9.10), (45.41, 9.11), (45.42, 9.12)])
    _pos(pdb, 2, [(45.50, 9.20), (45.51, 9.21)])

    may = db_reader.get_month_track("2026-05")
    assert len(may) == 1 and len(may[0]) == 1            # one trip, one solid run (no gap)
    assert may[0][0]["gap"] is False
    assert may[0][0]["points"][0] == [45.4, 9.1] and may[0][0]["points"][-1] == [45.42, 9.12]

    jun = db_reader.get_month_track("2026-06")
    assert len(jun) == 1 and len(jun[0]) == 1
    assert len(jun[0][0]["points"]) == 2

    assert db_reader.get_month_track("2026-04") == []   # month with no trips
    assert db_reader.get_month_track("") == []          # guard
