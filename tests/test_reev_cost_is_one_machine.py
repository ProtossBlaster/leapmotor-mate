"""One electric-cost machine for every page (Silvio's call, #207/#36 follow-up).

The trip detail always priced a REEV trip from the depleting PAID STOCK (`reev_trip_electric_cost` /
`_paid_stock_replay`), where generator kWh are free because they were already paid in litres. But the
list, the calendar, Statistics and the Monthly Report priced through `_localized_trips` → `_wac_blend`,
which raises a €/kWh nothing consumes — wrong for a range-extender, and it made the calendar disagree
with the detail (measured on @michapr: 9.01 € vs 11.35 €). `_localized_trips` now takes the REEV cost
from the same paid-stock replay, so every page agrees.

⚠️ A pure BEV must be BYTE-FOR-BYTE unchanged: on a BEV `_reev_electric_cost_by_trip()` returns {},
so the old `_wac_blend` branch runs exactly as before.
"""
import db as D
import db_reader
import pytest


@pytest.fixture
def bev(tmp_path, monkeypatch):
    path = str(tmp_path / "b.db")
    d = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    d._conn.execute("INSERT INTO vehicles (id,vin,car_type,capacity_kwh) VALUES (1,'V','B10',60)")
    d._conn.commit()
    return d


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "r.db")
    d = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    d._conn.execute("INSERT INTO vehicles (id,vin,car_type,capacity_kwh) VALUES (1,'V','B10',40)")
    d._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('is_reev','1')")
    d._conn.commit()
    return d


def _charge(d, day, cost, kwh, ss=20, es=80):
    d._conn.execute(
        "INSERT INTO charges (vehicle_id,started_at,ended_at,start_soc,end_soc,energy_added_kwh,cost,"
        "location_type) VALUES (1,?,?,?,?,?,?,'HOME')",
        (f"2026-07-{day:02d}T06:00:00+00:00", f"2026-07-{day:02d}T08:00:00+00:00", ss, es, kwh, cost))
    d._conn.commit()


def _trip(d, day, km, eff, ec=None):
    d._conn.execute(
        "INSERT INTO trips (vehicle_id,started_at,ended_at,distance_km,start_soc,end_soc,"
        "efficiency_kwh_100km,ec_kwh,ec_stable,duration_min) VALUES (1,?,?,?,70,55,?,?,?,40)",
        (f"2026-07-{day:02d}T09:00:00+00:00", f"2026-07-{day:02d}T10:00:00+00:00",
         km, eff, ec, 1 if ec else 0))
    d._conn.commit()


# ── the BEV must not move ─────────────────────────────────────────────────────

def test_a_bev_trip_is_still_priced_by_the_blend(bev):
    """The invariant Silvio insisted on: no regression for a pure EV. With no is_reev,
    `_reev_electric_cost_by_trip()` returns {} and the BEV branch (efficiency × distance × the blended
    €/kWh) runs exactly as before."""
    _charge(bev, 1, cost=6.0, kwh=30.0)             # blended rate = 6/30 = 0.20 €/kWh
    _trip(bev, 5, km=100.0, eff=15.0)               # energy 15 kWh → 15 × 0.20 = 3.00 €
    t = db_reader._localized_trips(db_reader.get_trips(1_000_000))[0]
    assert t["cost"] == pytest.approx(3.0)


# ── the REEV agrees with itself, everywhere ──────────────────────────────────

def test_localized_trips_matches_the_trip_detail_for_a_reev(reev):
    _charge(reev, 1, cost=6.0, kwh=30.0)
    _trip(reev, 5, km=100.0, eff=15.0, ec=10.0)
    _trip(reev, 6, km=120.0, eff=12.0, ec=8.0)
    for t in db_reader._localized_trips(db_reader.get_trips(1_000_000)):
        detail = db_reader.reev_trip_electric_cost(1, t["id"])
        assert detail is not None
        assert t["cost"] == pytest.approx(detail["cost"])


def test_the_calendar_month_cost_equals_the_sum_of_the_detail_costs(reev):
    """The whole point: the Trips calendar (and, through the same machinery, the Report) price a REEV
    month exactly as the sum of the per-trip detail costs."""
    _charge(reev, 1, cost=6.0, kwh=30.0)
    _trip(reev, 5, km=100.0, eff=15.0, ec=10.0)
    _trip(reev, 6, km=120.0, eff=12.0, ec=8.0)
    cal = db_reader.get_trips_calendar_month(2026, 7)
    detail_sum = sum((db_reader.reev_trip_electric_cost(1, t["id"]) or {}).get("cost", 0.0)
                     for t in db_reader.get_trips(1_000_000))
    assert cal["total"]["cost"] == pytest.approx(detail_sum, abs=0.01)


def test_generator_energy_comes_out_free_for_a_reev(reev):
    """A draw larger than the paid stock: the excess is FREE — generator kWh, already paid in litres.
    `_wac_blend` would have priced all of it at grid rate; the paid stock prices only what was bought."""
    _charge(reev, 1, cost=6.0, kwh=10.0)            # only 10 kWh bought (0.60 €/kWh)
    _trip(reev, 5, km=200.0, eff=15.0, ec=25.0)     # draws 25 kWh: 10 paid (6 €) + 15 free
    t = db_reader._localized_trips(db_reader.get_trips(1_000_000))[0]
    assert t["cost"] == pytest.approx(6.0)          # the 15 generator kWh cost nothing
