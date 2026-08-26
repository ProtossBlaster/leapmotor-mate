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


def test_the_trend_recovers_a_known_line(tmp_path, monkeypatch):
    """eff = 15 + 0.5 × (20 − T): exactly −5 kWh/100km per +10 °C. Eight points minimum."""
    db = _setup(tmp_path, monkeypatch)
    for i, T in enumerate(range(-4, 36, 5)):          # 8 temperatures, -4…31 °C
        _trip(db, i + 1, 50.0, round(15 + 0.5 * (20 - T), 2), t_start=T, t_end=T, day=i + 1)
    trend = db_reader.get_efficiency_vs_temp()["trend"]
    assert trend is not None
    assert trend["per_10c"] == pytest_approx(-5.0)
    assert trend["r2"] > 0.99


def pytest_approx(v):
    import pytest
    class _A:
        def __eq__(self, other): return abs(other - v) < 0.01
    return _A()


def test_fewer_than_eight_points_refuse_a_trend(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    for i in range(3):
        _trip(db, i + 1, 50.0, 16.0, t_start=10.0 + i, t_end=None, day=i + 1)
    assert db_reader.get_efficiency_vs_temp()["trend"] is None


def test_the_cap_keeps_the_newest_500(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    for i in range(12):
        _trip(db, i + 1, 50.0, 15.0 + i, t_start=float(i), t_end=None, day=(i % 28) + 1)
    out = db_reader.get_efficiency_vs_temp(limit=5)
    assert len(out["points"]) == 5
    assert [p["id"] for p in out["points"]] == [8, 9, 10, 11, 12]   # newest kept


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

import json
import pathlib

LOCALES = sorted((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales").glob("*.json"))
_KEYS = ("stats_efftemp_title", "stats_efftemp_hint", "stats_efftemp_electric",
         "stats_efftemp_petrol", "stats_efftemp_y", "stats_efftemp_fuel_y",
         "stats_efftemp_trend_elec", "stats_efftemp_trend_fuel")


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
