"""Asking whether V2L was used must not walk a week of position rows.

Measured on production (Home Assistant add-on, aarch64) on 28/09/2026, inside the container so no
network is involved: `get_v2l_status` costs **57.4 ms**, and `_ctx` calls it for the Overview on
every render while the Overview's own card refreshes every 10 s. It costs that because
`get_v2l_sessions` SELECTs every position row of the last seven days — thousands of them — builds a
dict per row and walks them in Python to find sessions, and then the caller keeps only the last one.

A V2L session is rare and leaves a mark: `ac_port_mode = 2`. When the window holds no such sample
there is no session to find, and the answer is the idle one. So ask that first, in SQL, and walk
the frames only when there is something to walk.

The behaviour must not move: the same sessions, the same figures, whenever V2L WAS used.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sys.path is conftest.py's job — it puts web/ before poller/, and BOTH hold a main.py.
# Re-inserting them here once flipped that order and nine other tests could not import the
# web app at all.

import db as D  # noqa: E402
import db_reader  # noqa: E402


class _CountingConnection:
    """The real connection, counting the rows each query hands back."""

    def __init__(self, inner):
        self._inner = inner
        self.rows = 0
        self.statements = []

    def execute(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        cursor = self._inner.execute(sql, *args)
        return _CountingCursor(cursor, self)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _CountingCursor:
    def __init__(self, inner, owner):
        self._inner = inner
        self._owner = owner

    def fetchall(self):
        rows = self._inner.fetchall()
        self._owner.rows += len(rows)
        return rows

    def __iter__(self):
        rows = list(self._inner)
        self._owner.rows += len(rows)
        return iter(rows)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def week_of_frames(tmp_path, monkeypatch):
    """One car, a week of parked frames every 30 s, and no V2L anywhere."""
    path = str(tmp_path / "frames.db")
    database = D.Database(path)
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    now = datetime.now(timezone.utc)
    conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charge_current_a, charge_voltage_v,"
        " ac_port_mode) VALUES (1, ?, 60, 0, 0, 0)",
        [((now - timedelta(seconds=30 * i)).isoformat(),) for i in range(7 * 24 * 120)])
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    counters = []

    real_get = db_reader._get

    def counting_get():
        counted = _CountingConnection(real_get())
        counters.append(counted)
        return counted

    monkeypatch.setattr(db_reader, "_get", counting_get)
    return counters


def test_a_week_without_v2l_is_answered_without_reading_the_week(week_of_frames):
    status = db_reader.get_v2l_status()
    assert status["ever_used"] is False and status["active"] is False
    read = sum(c.rows for c in week_of_frames)
    assert read < 100, (
        f"{read} rows read to say V2L was not used; the window holds 20160 frames and none of them "
        f"is a V2L sample"
    )


def test_a_session_is_still_found_and_still_adds_up(tmp_path, monkeypatch):
    """The early answer must not cost a real session: same figures as the walk always gave."""
    path = str(tmp_path / "v2l.db")
    database = D.Database(path)
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    now = datetime.now(timezone.utc)
    rows = []
    # positive current is discharge here, and the session's net power is (i - idle) * v
    for i in range(400):                       # quiet frames before it, setting the idle baseline
        rows.append(((now - timedelta(minutes=600 - i)).isoformat(), 80, 0.5, 230.0, 0))
    for i in range(20):                        # 20 minutes of V2L at (4.5 - 0.5) * 230 = 920 W
        rows.append(((now - timedelta(minutes=200 - i)).isoformat(), 80 - i * 0.1, 4.5, 230.0, 2))
    conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charge_current_a, charge_voltage_v,"
        " ac_port_mode) VALUES (1, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    out = db_reader.get_v2l_sessions(lookback_days=7, limit=50, vehicle_id=1)
    assert out["sessions"], "the session vanished"
    session = out["sessions"][-1]
    assert round(session["duration_min"]) == 19, session
    assert session["peak_w"] > 0 and session["energy_wh"] > 0, session

    status = db_reader.get_v2l_status()
    assert status["ever_used"] is True, status
