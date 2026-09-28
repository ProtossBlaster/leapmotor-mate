"""Which car we are looking at is one question, not eighty-seven.

Profiled on a real database on 28/09/2026: `get_battery_health` — built for every Settings page —
issues 178 queries, and **87 of them are the same one**, `_current_vehicle_id`'s
`SELECT id FROM vehicles ORDER BY vin <> COALESCE(...)`. Its own docstring says it "runs around
sixty times per page render". It cost 233.8 ms on a Mac, about 0.95 s on the add-on that prompted
this, and Settings was the slowest page left.

The reader already has a way to hold the answer — `_read_vehicle_scope`, used by the research
export so a car switched mid-export cannot change what is being exported. It is a `threading.local`
and this is an async app, so it is only safe around a SYNCHRONOUS stretch that awaits nothing; a
whole request would need contextvars and is not this change.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sys.path is conftest.py's job — it puts web/ before poller/, and BOTH hold a main.py.
# Re-inserting them here once flipped that order and nine other tests could not import the
# web app at all.

import db as D  # noqa: E402
import db_reader  # noqa: E402

WHICH_CAR = "SELECT id FROM vehicles ORDER BY vin"


class _Counting:
    def __init__(self, inner, seen):
        self._inner, self._seen = inner, seen

    def execute(self, sql, *args):
        if " ".join(sql.split()).startswith(WHICH_CAR):
            self._seen.append(sql)
        return self._inner.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def counted(tmp_path, monkeypatch):
    path = str(tmp_path / "battery.db")
    database = D.Database(path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    database._conn.commit()
    database._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    seen = []
    real = db_reader._get
    monkeypatch.setattr(db_reader, "_get", lambda: _Counting(real(), seen))
    return seen


def test_building_the_battery_card_asks_once(counted):
    db_reader.get_battery_health()
    assert len(counted) <= 2, f"asked which car {len(counted)} times to build one card"


def test_the_scope_is_given_back_afterwards(counted):
    """A pin left behind would freeze every later read on this thread's car."""
    before = getattr(db_reader._read_vehicle_scope, "vehicle_id", None)
    db_reader.get_battery_health()
    after = getattr(db_reader._read_vehicle_scope, "vehicle_id", None)
    assert after == before, f"the scope was left pinned at {after}"


def test_a_pin_already_held_is_not_disturbed(counted):
    """The research export pins the car it started with; nothing inside may repoint it."""
    db_reader._read_vehicle_scope.vehicle_id = (1,)
    try:
        db_reader.get_battery_health()
        assert db_reader._read_vehicle_scope.vehicle_id == (1,)
    finally:
        db_reader._read_vehicle_scope.vehicle_id = None
