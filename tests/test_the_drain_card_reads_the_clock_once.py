"""The standby-drain card must not ask which time zone it is in once per park.

Profiled inside the lab container against a real database on 28/09/2026, `get_vampire_drain` cost
**490 ms**, and the profile named the second half of it:

    1610 calls  0.200 s  {method 'execute' of 'sqlite3.Connection' objects}
    1608 calls  0.195 s  db_reader.py:878(get_setting)
    1610 calls  0.051 s  {built-in method _sqlite3.connect}
   43473 calls  0.202 s  db_reader.py:10081(_flush)

`_flush` closes a parked window and calls `_local_dt` twice — with no zone — so each one asks
`_local_tz`, which reads the `timezone` setting, and every one of those reads opens its own SQLite
connection. 1608 reads to draw one card.

This is the same defect that cost the Statistics page 1172 reads in 4.5.2, and `_local_tz`'s own
docstring warns about it. The five loops fixed then did not include this one, because the call sits
inside a closure rather than in the visible loop.

The figures must not move: the same windows, the same rejections, the same headline.
"""
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sys.path is conftest.py's job — it puts web/ before poller/, and BOTH hold a main.py.

import db as D  # noqa: E402
import db_reader  # noqa: E402


class _Counting:
    """Counts the reads of one settings key, whichever connection they arrive on."""

    def __init__(self, inner, seen):
        self._inner, self._seen = inner, seen

    def execute(self, sql, *args):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT value FROM settings WHERE key") and args and args[0]:
            self._seen.append(args[0][0] if isinstance(args[0], (tuple, list)) else args[0])
        return self._inner.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def many_parks(tmp_path, monkeypatch):
    """Thirty separate parks, each long enough to be measured, with a drive between them."""
    path = str(tmp_path / "drain.db")
    database = D.Database(path)
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','Europe/Rome')")
    start = datetime.now(timezone.utc) - timedelta(days=60)
    rows, soc, odo = [], 90.0, 1000.0
    for park in range(30):
        base = start + timedelta(days=park * 2)
        for step in range(13):                       # 12 hours parked, one frame an hour
            rows.append(((base + timedelta(hours=step)).isoformat(),
                         round(soc - step * 0.05, 2), 0, 0.0, odo, 0))
        soc -= 0.6
        drive = base + timedelta(hours=14)
        for step in range(4):                        # then a drive, which closes the park
            rows.append(((drive + timedelta(minutes=step)).isoformat(),
                         round(soc, 2), 0, 40.0, odo + step * 2, 1))
        odo += 10
        soc -= 2
    conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh, odometer_km,"
        " ready) VALUES (1, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    seen = []
    real = db_reader._get
    monkeypatch.setattr(db_reader, "_get", lambda: _Counting(real(), seen))
    return seen


def test_the_clock_is_read_once_for_the_whole_card(many_parks):
    out = db_reader.get_vampire_drain()
    assert out["measurable_count"] >= 20, f"the fixture built no parks: {out['measurable_count']}"
    zone_reads = [k for k in many_parks if k == "timezone"]
    assert len(zone_reads) <= 2, (
        f"asked which time zone it is in {len(zone_reads)} times to draw one card, once per park "
        f"closed; each read opens its own SQLite connection"
    )


def test_the_windows_are_the_same_ones(many_parks):
    """Hoisting the zone must change the cost and nothing else."""
    out = db_reader.get_vampire_drain()
    assert out["count"] >= 20 and out["windows"], out
    first = out["windows"][0]
    for key in ("start", "end", "hours", "drop_pct", "pct_per_day", "reliable"):
        assert key in first, f"{key} lost from a window"
    assert first["hours"] == pytest.approx(12.0, abs=0.1), first
    assert out["typical_pct_per_day"] is not None


def test_the_polling_card_reads_the_clock_once(many_parks):
    """Same defect, same card that made 4.5.0 slow: 288 windows with two ends each.

    Profiled on the lab: `polling_summary` cost 579 queries and **578 of them were the timezone**.
    The zone was already resolved at the top of the function — the closure that formats each
    window's two times just did not take it.
    """
    before = len([k for k in many_parks if k == "timezone"])
    db_reader.polling_summary()
    reads = len([k for k in many_parks if k == "timezone"]) - before
    assert reads <= 2, f"the polling strip asked which time zone it is in {reads} times"


@pytest.fixture
def eight_charges(tmp_path, monkeypatch):
    """Eight real charges with telemetry, so the SoH chart actually has points to date.

    The first version of this fixture had no charges at all: the chart came back empty, the per-point
    localisation never ran, and the test passed with the defect still in place — a green asserting
    the bug.
    """
    path = str(tmp_path / "soh.db")
    database = D.Database(path)
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','Europe/Rome')")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('battery_capacity_kwh','60')")
    base = datetime.now(timezone.utc) - timedelta(days=80)
    frames = []
    for charge in range(8):
        start = base + timedelta(days=charge * 9)
        end = start + timedelta(hours=3)
        conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
                     " charge_type) VALUES (1, ?, ?, 30, 80, 'AC')",
                     (start.isoformat(), end.isoformat()))
        for step in range(181):              # one frame a minute at 10 kW → ~30 kWh over 50 points
            frames.append(((start + timedelta(minutes=step)).isoformat(),
                           30 + step * 50 / 180.0, 1, 400.0, 25.0, 20.0, 1000.0 + charge * 10))
    conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, charge_voltage_v,"
        " charge_current_a, battery_min_temp, odometer_km) VALUES (1, ?, ?, ?, ?, ?, ?, ?)", frames)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    seen = []
    real = db_reader._get
    monkeypatch.setattr(db_reader, "_get", lambda: _Counting(real(), seen))
    return seen


def test_the_battery_chart_reads_the_clock_once(eight_charges):
    """One read per charge dated, on a chart that can hold years of them."""
    out = db_reader.get_battery_health()
    assert len(out["points"]) >= 6, f"the fixture produced no chart: {out}"
    reads = len([k for k in eight_charges if k == "timezone"])
    assert reads <= 2, (
        f"the SoH chart asked which time zone it is in {reads} times for "
        f"{len(out['points'])} points"
    )


def test_the_drain_card_does_not_build_a_row_object_per_frame(many_parks):
    """Ninety days is 284 505 frames on a real database, and each one was becoming a `sqlite3.Row`.

    Measured inside the lab container: reading that window and walking it costs **220.43 ms** with
    `row_factory = sqlite3.Row` and `fetchall()`, and **150.65 ms** reading plain tuples off the
    cursor — 68%, with the same count of frames. Nothing about the arithmetic changes; what goes is
    a per-frame object and a per-field name lookup, on the one read in Mate that is measured in
    hundreds of thousands of rows.
    """
    seen = {}
    real = db_reader._get()

    class _Watch:
        def execute(self, sql, *args):
            cursor = real.execute(sql, *args)
            if "FROM positions" in sql and "ac_port_mode" in sql:
                seen["cursor"] = cursor
            return cursor

        def __getattr__(self, name):
            return getattr(real, name)

    original = db_reader._get
    db_reader._get = lambda: _Watch()
    try:
        db_reader.get_vampire_drain()
    finally:
        db_reader._get = original

    assert "cursor" in seen, "the window read is not where this test expects it"
    assert seen["cursor"].row_factory is None, (
        "the drain card still turns every one of ninety days of frames into a sqlite3.Row"
    )


def test_the_times_are_still_local(many_parks):
    """The zone is passed down, not dropped: a window still carries a Rome offset."""
    out = db_reader.get_vampire_drain()
    assert out["windows"], out
    assert "+0" in out["windows"][0]["start"][-6:] or "+" in out["windows"][0]["start"], (
        f"the window's start lost its local offset: {out['windows'][0]['start']}"
    )
