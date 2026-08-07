"""A merged trip's petrol is the WHOLE group's, on the detail page as well as the list.

Found 07/08/26 while checking beta #27 on the demo container, not reported by anyone.

`get_trip_detail` read the fuel PERCENTAGES off the group and the LITRES off `_tp` — the parent
row — one line apart, and directly under a comment saying the opposite:

    # From the GROUP, not from `_tp` (the parent row): on a merged trip the parent is only the
    # first segment, and its tank says nothing about what the later segments burned — see beta #20.

`_reev_trip_fuel` prefers the measured litres whenever they are present, so the parent's won and
every later segment's petrol was dropped. That is beta #20 coming back through the millilitre path
added in v2.14.1, which the percentage fix of the time never covered — and `merge_trips` writes
only `merged_into_id`, so nothing ever rewrites the parent's litres afterwards.

Measured on a 30 + 30 km group that burned 2.9 L: the trips list said **4.8 L/100km** (get_trips
has always passed the group's figures) and the detail page said **2.5** — the same drive, two
pages, two answers. Not research-gated, so it reached every range-extender owner who merges trips.

⚠️ Only the LITRES are asserted here. Whether the parent's `ec_kwh` stands for the group depends on
whether the owner ran convert-on-merge afterwards, so it is a separate question — not one to settle
by assertion.
"""
import db as D
import db_reader
import pytest


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    database.set_setting("is_reev", "1")
    database._conn.commit()
    return database


def _seg(db, tid, *, hour, km, fuel_from_l, fuel_to_l, fuel_from_pct, fuel_to_pct, parent=None):
    db._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
        " start_soc, end_soc, efficiency_kwh_100km, ec_kwh, ec_stable, ec_tried,"
        " fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l, merged_into_id)"
        " VALUES (?,1,?,?,?,30,66,63,NULL,1.5,1,1,?,?,?,?,?)",
        (tid, f"2026-08-07T{hour:02d}:00:00+00:00", f"2026-08-07T{hour:02d}:30:00+00:00",
         km, fuel_from_pct, fuel_to_pct, fuel_from_l, fuel_to_l, parent))
    db._conn.commit()


def _merged_group(db):
    """Two 30 km segments. The parent burned 1.5 L, the child 1.4 — the group burned 2.9."""
    _seg(db, 1, hour=13, km=30.0, fuel_from_l=32.9, fuel_to_l=31.4,
         fuel_from_pct=66.0, fuel_to_pct=63.0)
    _seg(db, 2, hour=14, km=30.0, fuel_from_l=31.4, fuel_to_l=30.0,
         fuel_from_pct=63.0, fuel_to_pct=60.0, parent=1)
    return db_reader.get_trip_detail(1)


def test_the_detail_counts_every_segments_petrol(reev):
    """🔴 The defect. 2.9 L burned across the group; the page used to show the parent's 1.5."""
    d = _merged_group(reev)
    assert d["distance_km"] == 60.0, "the group distance is the premise of the rest"
    assert d["fuel_used_l"] == pytest.approx(2.9, abs=0.01)


def test_the_rate_follows_the_litres(reev):
    """2.9 L over 60 km = 4.833 → 4.8 L/100km. The old figure, 1.5 over 60, gave 2.5 — a drive
    reading barely half as thirsty as it was."""
    assert _merged_group(reev)["fuel_l_100km"] == pytest.approx(4.8, abs=0.05)


def test_the_detail_and_the_list_now_agree(reev):
    """The shape of the whole bug: get_trips has always passed the GROUP's figures, so the two
    pages printed different petrol for one drive. Neither number alone would have looked wrong."""
    detail = _merged_group(reev)
    row = next(r for r in db_reader.get_trips() if r["id"] == 1)
    assert row["fuel_used_l"] == pytest.approx(detail["fuel_used_l"], abs=0.01)
    assert row["fuel_l_100km"] == pytest.approx(detail["fuel_l_100km"], abs=0.05)


def test_an_unmerged_trip_is_unchanged(reev):
    """The group of one: `_trip_group_stats` returns the parent untouched, so a plain trip must
    read exactly as it always has — this is the regression the one-word change could cause."""
    _seg(reev, 1, hour=13, km=30.0, fuel_from_l=32.9, fuel_to_l=31.4,
         fuel_from_pct=66.0, fuel_to_pct=63.0)
    d = db_reader.get_trip_detail(1)
    assert d["fuel_used_l"] == pytest.approx(1.5, abs=0.01)
    assert d["fuel_l_100km"] == pytest.approx(5.0, abs=0.05)


def test_the_group_reads_the_segments_that_actually_have_a_counter(reev):
    """A segment can carry no millilitre counter (a trip recorded before v2.14.1). Here the PARENT
    is the one without it, so blindly taking the first segment's `fuel_start_l` yields None and the
    whole reading falls back to the coarse percentage path — a different number from a different
    signal, silently.

    ⚠️ The first version of this test only asserted `> 0`, which the broken code satisfies too: the
    percentage fallback also returns something positive. A test that cannot go red is not a test.
    Pinned to the child's own 1.4 L, which only the first-that-exists rule produces."""
    _seg(reev, 1, hour=13, km=30.0, fuel_from_l=None, fuel_to_l=None,
         fuel_from_pct=66.0, fuel_to_pct=63.0)
    _seg(reev, 2, hour=14, km=30.0, fuel_from_l=31.4, fuel_to_l=30.0,
         fuel_from_pct=63.0, fuel_to_pct=60.0, parent=1)
    assert db_reader.get_trip_detail(1)["fuel_used_l"] == pytest.approx(1.4, abs=0.01)
