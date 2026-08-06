"""What the GENERATOR itself drinks, over the kilometres it actually drove.

@michapr, BetaTester #26, 06/08/26, in the same breath as the denominator defect:

    «The generator's consumption rate during operation — which is certainly an interesting technical
    metric — should be included on the statistics page, alongside the comparable figure for kWh
    consumption.»

He is asking for the third of the three numbers his own data produces, and the only one Mate never
showed:

    2.0 L/100 km   over all 479 km                      ← the Trips strip, the car's own basis
    5.85 L/100 km  over the 164 km of generator trips   ← nobody's figure; the defect he reported
    15.2 L/100 km  over the 63 km the generator drove   ← THIS one

⚠️ **Two L/100 km on one page, seven times apart.** That is the shape Silvio named — two correct
numbers under one word is a defect regardless of either being right. So this one never sits beside
the other as a bare figure: the label says *while running*, in every language, next to the number.

🔑 The litres come from `total_l` — the car's own millilitre counter where the trip carries it — and
NOT from `engine_l`, which the same loop already computes as `engine_fuel_pct × nominal tank`. Both
are available; one is measured and one is a percentage against a nominal 50 litres. Dividing the
measured litres by the measured distance is the only pairing where both halves come from the car.
"""
import pathlib

import db as PollerDB
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


@pytest.fixture
def trips(tmp_path, monkeypatch):
    pdb = PollerDB.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)
    db_reader.set_setting("is_reev", "1")

    def add(day, km, litres=None):
        """No positions, so `_reev_engine_on` returns None and the generator distance falls back to
        the whole trip — a real production path (pruned trails), and the one that keeps this test
        about the arithmetic rather than about walking a signal trail."""
        l0 = 40.0
        l1 = l0 - litres if litres else l0
        pdb._conn.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc,"
            " end_soc, fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l)"
            " VALUES (?,1,?,?,?,80,60,?,?,?,?)",
            (day, f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T10:00:00+00:00",
             km, l0 / 0.5, l1 / 0.5, l0, l1))
        pdb._conn.commit()
    add.db = pdb            # the reader's own connection is READ-ONLY; write through the poller
    return add


def test_the_rate_divides_by_the_kilometres_the_generator_drove(trips):
    """6 L over the 100 km it ran for is 6.0 L/100km — whatever else the car did that month."""
    trips(1, km=100.0, litres=6.0)
    trips(2, km=300.0)
    s = db_reader.reev_fuel_summary()
    assert s["engine_km"] == 100.0
    assert s["engine_l_100km"] == 6.0


def test_it_is_not_the_figure_standing_next_to_it(trips):
    """The whole reason this needs a label: same unit, same card, seven times apart on his data."""
    trips(1, km=100.0, litres=6.0)
    trips(2, km=300.0)
    s = db_reader.reev_fuel_summary()
    assert s["avg_l_100km"] == 1.5, "spread over every kilometre driven"
    assert s["engine_l_100km"] == 6.0, "spread over the generator's own kilometres"
    assert s["engine_l_100km"] != s["avg_l_100km"]


def test_his_own_proportion(trips):
    """His bundle: 9.6 L, and the generator drove 63 of the 479 km."""
    trips(1, km=63.0, litres=9.6)
    trips(2, km=416.0)
    s = db_reader.reev_fuel_summary()
    assert s["avg_l_100km"] == 2.0
    assert s["engine_l_100km"] == 15.2


def test_a_month_with_no_generator_has_no_rate(trips):
    trips(1, km=100.0)
    trips(2, km=300.0)
    assert db_reader.reev_fuel_summary() is None


def test_the_litres_are_the_measured_ones_not_the_tank_percentage(trips):
    """`engine_l` in the same loop is `engine_fuel_pct × nominal tank`; `total_l` prefers the car's
    own millilitre counter. Only a trip WITH a position trail can tell them apart — on the pruned
    fallback the loop sets `engine_l = drop_l`, so the two are equal and an assertion here proves
    nothing. This test was green on that path before it was given a trail; it is the sixth time on
    this repo a test has passed while asserting nothing.

    So: a real trail, 100 km, generator running throughout. The tank percentage falls 16% of a
    nominal 50 L tank → 8.0 L. The car's own counter says 6.0. The figure must read 6."""
    trips(1, km=100.0, litres=6.0)
    trips.db._conn.execute("UPDATE trips SET fuel_start_pct=100.0, fuel_end_pct=84.0 WHERE id=1")
    # ⚠️ Inside the trip's own window (08:00→10:00): `_reev_engine_on` selects BETWEEN those two,
    # and a sample at 10:30 is silently dropped — which is how the first version of this walked
    # half the trail and reported 50 km.
    for ts, odo, pct in [("08:30", 1000.0, 100.0), ("09:00", 1050.0, 92.0), ("09:30", 1100.0, 84.0)]:
        trips.db._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, odometer_km, fuel_level_pct)"
            " VALUES (1,?,?,?)", (f"2026-07-01T{ts}:00+00:00", odo, pct))
    trips.db._conn.commit()

    s = db_reader.reev_fuel_summary()
    assert s["engine_km"] == 100.0, "the trail was walked, not fallen back on"
    assert s["engine_l_100km"] == 6.0, "the tank percentage would have made this 8.0"


# ── on the page, and named ────────────────────────────────────────────────────

def test_the_card_shows_it():
    body = (ROOT / "web" / "templates" / "statistics.html").read_text()
    assert "engine_l_100km" in body, "computed and never rendered"


def test_the_label_exists_and_says_while_running_in_every_language():
    import json
    for path in LOCALES:
        flat = {k: v for s in json.loads(path.read_text()).values()
                if isinstance(s, dict) for k, v in s.items()}
        assert "stats_reev_gauges_engine" in flat, f"{path.name} is missing the label"
        assert flat["stats_reev_gauges_engine"].strip(), f"{path.name}: the label is empty"
