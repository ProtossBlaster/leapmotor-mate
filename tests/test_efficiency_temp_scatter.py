"""The consumption-vs-temperature scatter (Statistics card).

One point per finished trip against the OUTSIDE air temperature the elevation enrichment already
put on the row — no new cloud calls. The question it answers is the one every owner asks when the
cold arrives: *how much does MY car really drink at 5°C?*, read off their own winter and their own
summer rather than a brochure.

Three invariants the tests hold onto, because each one was a way to quietly lie:

  • on a REEV the electric series is battery-only BY CONSTRUCTION — finalize_trip blanks
    efficiency_kwh_100km on every generator-on trip, so a generator trip can never pose as an
    electric point;
  • the petrol series exists only behind the usual is_reev+research gate (`include_fuel`), and a
    mid-trip refuel never becomes a point (its litres are unknowable);
  • short trips stay out: preconditioning spread over three kilometres reads as a cold penalty
    that isn't one.
"""
import json

import pytest
from datetime import datetime, timedelta, timezone

import db as D
import db_reader


def _setup(tmp_path, monkeypatch, car_type="B10"):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute(
        "INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VINX',?)", (car_type,))
    database._conn.commit()
    return database


def _trip(db, tid, km, eff, t_start=None, t_end=None, day=1,
          fuel_from=None, fuel_to=None, merged_into=None):
    db._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " efficiency_kwh_100km, outside_temp_start_c, outside_temp_end_c,"
        " fuel_start_pct, fuel_end_pct, merged_into_id)"
        " VALUES (?,1,?,?,?,?,?,?,?,?,?)",
        (tid, f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T09:00:00+00:00",
         km, eff, t_start, t_end, fuel_from, fuel_to, merged_into))
    db._conn.commit()


# ── BEV: the points, the bins, the trend ─────────────────────────────────────

def test_points_are_oldest_first_and_temps_are_averaged(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    _trip(db, 1, 50.0, 20.0, t_start=5.0, t_end=7.0, day=1)     # avg 6.0
    _trip(db, 2, 50.0, 15.0, t_start=25.0, t_end=None, day=2)   # start only → 25.0
    _trip(db, 3, 50.0, 14.0, t_start=None, t_end=28.0, day=3)   # end only → 28.0
    out = db_reader.get_efficiency_vs_temp()
    assert [p["id"] for p in out["points"]] == [1, 2, 3]
    assert [p["t"] for p in out["points"]] == [6.0, 25.0, 28.0]
    assert [p["e"] for p in out["points"]] == [20.0, 15.0, 14.0]


def test_short_and_temperatureless_trips_stay_out(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    _trip(db, 1, 2.9, 30.0, t_start=5.0, t_end=5.0)   # below min_km: precond noise
    _trip(db, 2, 40.0, 18.0, t_start=None, t_end=None)  # no temperature at all
    _trip(db, 3, 40.0, 16.0, t_start=12.0, t_end=14.0)
    out = db_reader.get_efficiency_vs_temp()
    assert [p["id"] for p in out["points"]] == [3]
    assert out["bins"] == [{"lo": 10, "hi": 15, "mean": 16.0, "n": 1}]


def test_a_merged_child_is_its_parents_business_only(tmp_path, monkeypatch):
    """A trip folded into another must not surface as its own cold morning."""
    db = _setup(tmp_path, monkeypatch)
    _trip(db, 1, 60.0, 19.0, t_start=10.0, t_end=12.0)
    _trip(db, 2, 5.0, 40.0, t_start=-5.0, t_end=-4.0, merged_into=1)   # child
    out = db_reader.get_efficiency_vs_temp()
    assert [p["id"] for p in out["points"]] == [1]



def test_a_merged_trip_is_its_whole_group(tmp_path, monkeypatch):
    """A 2 km first segment merged with the 20 km that followed is one 22 km trip: its distance,
    its consumption and its temperatures are the group's, as on the Trips page."""
    db = _setup(tmp_path, monkeypatch)
    for tid, start, end, km, odo, soc, eff, temps, parent in (
            (1, "08:00", "08:10", 2.0, (1000, 1002), (80, 79), 30.0, (4.0, 4.0), None),
            (2, "08:15", "09:00", 20.0, (1002, 1022), (79, 75), 15.0, (5.0, 6.0), 1)):
        db._conn.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_odometer_km,"
            " end_odometer_km, start_soc, end_soc, efficiency_kwh_100km, outside_temp_start_c,"
            " outside_temp_end_c, merged_into_id) VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"2026-07-01T{start}:00+00:00", f"2026-07-01T{end}:00+00:00", km, *odo, *soc, eff,
             *temps, parent))
    db._conn.commit()
    out = db_reader.get_efficiency_vs_temp()
    energy = 5 / 100 * db_reader.get_battery_capacity_kwh()
    assert [(p["id"], p["km"], p["t"]) for p in out["points"]] == [(1, 22.0, 5.0)]
    assert out["points"][0]["e"] == round(energy / 22 * 100, 1)


def test_a_dot_reads_the_consumption_the_trips_list_shows(tmp_path, monkeypatch):
    """Each dot takes its energy where the Trips list does: the cloud's record of the drive, else
    getEC, else the battery's SoC; a merged trip sums its segments' records."""
    db = _setup(tmp_path, monkeypatch)
    db.set_setting("is_reev", "0")
    _trip(db, 1, 50.0, 20.0, t_start=5.0, t_end=5.0, day=1)    # the cloud: 9 kWh → 18.0
    _trip(db, 2, 50.0, 17.0, t_start=6.0, t_end=6.0, day=2)    # getEC: 7.5 kWh → 15.0
    _trip(db, 3, 50.0, 16.0, t_start=7.0, t_end=7.0, day=3)    # SoC alone
    _trip(db, 4, 30.0, 19.0, t_start=8.0, t_end=8.0, day=4)    # merged, the cloud: 4 + 3 kWh over 50 km
    _trip(db, 5, 20.0, 19.0, t_start=8.0, t_end=8.0, day=5, merged_into=4)
    db._conn.execute("UPDATE trips SET ec_kwh = 7.5, ec_stable = 1 WHERE id = 2")
    db._conn.execute("CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records"
                     " (id INTEGER PRIMARY KEY, kind TEXT, payload_json TEXT)")
    for day, kwh, km in ((1, 9.0, 50.0), (4, 4.0, 30.0), (5, 3.0, 20.0)):
        a = datetime(2026, 7, day, 8, tzinfo=timezone.utc)
        rec = {"vin": "VINX", "routeStartTs": int(a.timestamp() * 1000),
               "routeEndTs": int((a + timedelta(hours=1)).timestamp() * 1000),
               "totalEnergy": kwh, "totalMileage": km}
        db._conn.execute("INSERT INTO api_lab_cloud_history_records (kind, payload_json)"
                         " VALUES ('mileage', ?)", (json.dumps(rec),))
    db._conn.commit()
    dots = {p["id"]: p["e"] for p in db_reader.get_efficiency_vs_temp()["points"]}
    assert dots == {1: 18.0, 2: 15.0, 3: 16.0, 4: 14.0}
    listed = {t["id"]: round(t["efficiency_kwh_100km"], 2) for t in db_reader.get_trips()}
    assert dots == listed


def test_the_trend_recovers_a_known_line(tmp_path, monkeypatch):
    """eff = 15 + 0.5 × (20 − T): a straight line is a cubic with no bend (D#498) — a and b come
    back as nothing, c as −0.5 per °C. Eight points minimum."""
    db = _setup(tmp_path, monkeypatch)
    for i, T in enumerate(range(-4, 36, 5)):          # 8 temperatures, -4…31 °C
        _trip(db, i + 1, 50.0, round(15 + 0.5 * (20 - T), 2), t_start=T, t_end=T, day=i + 1)
    trend = db_reader.get_efficiency_vs_temp()["trend"]
    assert trend is not None
    assert trend["a"] == pytest_approx(0.0) and trend["b"] == pytest_approx(0.0)
    assert trend["c"] == pytest_approx(-0.5)
    assert trend["d"] == pytest_approx(25.0)
    assert trend["r2"] > 0.99


def pytest_approx(v):
    import pytest
    class _A:
        def __eq__(self, other): return abs(other - v) < 0.01
    return _A()



def test_the_trend_is_consumption_per_kilometre_driven(tmp_path, monkeypatch):
    """At every temperature a 3 km hop at 30 and a 47 km drive at 15 kWh/100km: 0.9 + 7.05 kWh over
    50 km is 15.9 kWh/100km driven, and the curve says that, not the trips' mean of 22.5."""
    db = _setup(tmp_path, monkeypatch)
    for i, T in enumerate(range(-4, 36, 5)):
        _trip(db, 2 * i + 1, 3.0, 30.0, t_start=float(T), day=i + 1)
        _trip(db, 2 * i + 2, 47.0, 15.0, t_start=float(T), day=i + 1)
    tr = db_reader.get_efficiency_vs_temp()["trend"]
    assert [round(tr[k], 6) for k in ("a", "b", "c")] == [0, 0, 0]
    assert round(tr["d"], 6) == 15.9



@pytest.mark.parametrize("temps,km", [((0, 5, 10, 20), 50.0), ((30.0, 30.1, 30.2, 30.3), 50.0),
                                      ((0, 5, 10, 20), 5000.0)],
                         ids=["spread", "close-and-far-from-zero", "weights-a-hundred-times"])
def test_the_band_is_how_well_the_curve_is_known(tmp_path, monkeypatch, temps, km):
    """Two trips at each of four temperatures, 16 ± 1 kWh/100km: the cubic passes through the four
    means, and the 95 % interval there is that of a mean of two, k·1 with k = t(97.5 %, 4 dof) —
    however close the temperatures, however far from 0 °C, whatever unit the weights are in."""
    db = _setup(tmp_path, monkeypatch)
    for i, T in enumerate(temps):
        _trip(db, 2 * i + 1, km, 15.0, t_start=float(T), day=i + 1)
        _trip(db, 2 * i + 2, km, 17.0, t_start=float(T), day=i + 1)
    tr = db_reader.get_efficiency_vs_temp()["trend"]
    assert tr["k"] == 2.776
    for T in temps:
        z = (T - tr["mid"]) / tr["half"]
        g = [1, z, z ** 2, z ** 3]
        var = sum(g[r] * tr["cov"][r][c] * g[c] for r in range(4) for c in range(4))
        assert var == pytest.approx(1.0, abs=1e-9)


def test_fewer_than_eight_points_refuse_a_trend(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    for i in range(3):
        _trip(db, i + 1, 50.0, 16.0, t_start=10.0 + i, t_end=None, day=i + 1)
    assert db_reader.get_efficiency_vs_temp()["trend"] is None


def test_the_chart_reads_the_two_years_up_to_the_newest_trip(tmp_path, monkeypatch):
    """Not the newest 500, which lose whole seasons: last winter stays however many trips were
    driven since, and a trip more than two years before the newest one is out."""
    db = _setup(tmp_path, monkeypatch)
    for tid, when, temp in ((1, "2024-01-10", -9.0), (2, "2025-01-10", -8.0)):
        db._conn.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km,"
            " outside_temp_start_c) VALUES (?,1,?,?,50,24,?)",
            (tid, f"{when}T08:00:00+00:00", f"{when}T09:00:00+00:00", temp))
    db._conn.commit()
    for i in range(600):
        _trip(db, i + 3, 50.0, 15.0, t_start=18.0, day=2)               # 2026-07-02
    out = db_reader.get_efficiency_vs_temp()
    assert len(out["points"]) == 601 and out["points"][0]["id"] == 2


# ── REEV: two series, two gates ──────────────────────────────────────────────

def test_on_a_reev_the_electric_series_is_battery_only_by_construction(tmp_path, monkeypatch):
    """A generator trip has NO stored efficiency (finalize blanks it), so it cannot leak into the
    electric scatter even though the car plainly moved and used the pack underneath."""
    db = _setup(tmp_path, monkeypatch, car_type="C10")
    db.set_setting("is_reev", "1"); db._conn.commit()
    _trip(db, 1, 80.0, 16.0, t_start=8.0, t_end=9.0, fuel_from=None, fuel_to=None)      # pure EV
    _trip(db, 2, 80.0, None, t_start=2.0, t_end=3.0, fuel_from=60.0, fuel_to=55.0, day=2)  # generator
    out = db_reader.get_efficiency_vs_temp(include_fuel=True)
    assert [p["id"] for p in out["points"]] == [1]              # electric: only the quiet trip
    assert [p["id"] for p in out["fuel_points"]] == [2]         # petrol: only the burning one
    assert all(p["l"] > 0 for p in out["fuel_points"])
    assert out["fuel_bins"], "the petrol series carries its own bins"


def test_without_the_gate_no_petrol_ever_leaves_the_box(tmp_path, monkeypatch):
    """include_fuel=False (a BEV, or the official build facing a REEV) strips the fuel series even
    from a database full of engine-on trips — computed for nobody else, not merely hidden."""
    db = _setup(tmp_path, monkeypatch, car_type="C10")
    db.set_setting("is_reev", "1"); db._conn.commit()
    _trip(db, 1, 80.0, None, t_start=2.0, t_end=3.0, fuel_from=60.0, fuel_to=55.0)
    out = db_reader.get_efficiency_vs_temp()
    assert out["fuel_points"] == [] and out["fuel_bins"] == [] and out["fuel_trend"] is None


def test_a_mid_trip_refuel_is_never_a_point(tmp_path, monkeypatch):
    """The tank ROSE while driving: those litres are unknowable, so the trip stays out of the
    petrol scatter instead of reading as negative consumption."""
    db = _setup(tmp_path, monkeypatch, car_type="C10")
    db.set_setting("is_reev", "1"); db._conn.commit()
    _trip(db, 1, 100.0, None, t_start=6.0, t_end=7.0, fuel_from=30.0, fuel_to=90.0)
    out = db_reader.get_efficiency_vs_temp(include_fuel=True)
    assert out["fuel_points"] == []


# ── the card speaks every language the page does ─────────────────────────────

import pathlib

LOCALES = sorted((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales").glob("*.json"))
_KEYS = ("stats_efftemp_title", "stats_efftemp_hint", "stats_efftemp_electric",
         "stats_efftemp_petrol", "stats_efftemp_y", "stats_efftemp_fuel_y",
         "stats_efftemp_trend_elec", "stats_efftemp_trend_fuel", "stats_efftemp_trend_formula",
         "stats_efftemp_band")


def test_every_language_carries_the_card_strings():
    assert len(LOCALES) >= 8, [p.name for p in LOCALES]
    missing = []
    for p in LOCALES:
        tr = json.loads(p.read_text())["translations"]
        missing += [f"{p.stem}:{k}" for k in _KEYS if not tr.get(k)]
        for k in ("stats_efftemp_hint", "stats_efftemp_trend_elec", "stats_efftemp_trend_fuel"):
            if tr.get(k) and "{" not in tr[k]:
                missing.append(f"{p.stem}:{k} lost its placeholder")
    assert not missing, missing
