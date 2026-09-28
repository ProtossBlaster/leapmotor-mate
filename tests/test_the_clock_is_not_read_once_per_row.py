"""Showing a hundred dates must not ask the database a hundred times what the timezone is.

Profiled on a real database on 28/09/2026, one open of the Statistics page: **1207 queries, and
1176 of them were `SELECT value FROM settings WHERE key=?`** — 1172 of those for the key `timezone`,
from `_local_tz`, once per row localised, each on its own SQLite connection. The page cost 162 ms
on a Mac and about four times that on the add-on that prompted this.

`_local_tz` already memoises the ZoneInfo it builds; what it does not memoise is the read that tells
it which zone — and it must not. A held value is stale the moment the OTHER process writes the
setting, or a test writes it straight into the table, and the first attempt at a two-second cache
turned ten tests red for exactly that reason. So the zone is resolved ONCE per loop and handed to
`_local_dt(tz=...)`: no shared state, nothing to invalidate, and the read happens once per page
instead of once per row.
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


@pytest.fixture
def counted(tmp_path, monkeypatch):
    """A database with a timezone set, and a count of every settings read that asks for it."""
    path = str(tmp_path / "clock.db")
    database = D.Database(path)
    database._conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','Europe/Rome')")
    database._conn.commit()
    database._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader._invalidate_timezone()

    reads = []
    real = db_reader.get_setting

    def counting(key, default=None, *args, **kwargs):
        if key == "timezone":
            reads.append(key)
        return real(key, default, *args, **kwargs)

    monkeypatch.setattr(db_reader, "get_setting", counting)
    return reads


def test_a_list_of_trips_costs_one_read_not_one_per_trip(counted):
    """The path that cost 1172 reads: the localisation every trips page and the Statistics page
    share."""
    trips = [{"id": i, "started_at": "2026-03-14T%02d:00:00+00:00" % (i % 24), "distance_km": 10.0}
             for i in range(200)]
    db_reader._localized_trips(trips)
    assert len(counted) <= 2, f"{len(counted)} reads of the timezone to localise 200 trips"


def test_a_caller_with_the_zone_in_hand_reads_nothing(counted):
    zone = db_reader._local_tz()
    counted.clear()
    for minute in range(200):
        assert db_reader._local_dt("2026-03-14T%02d:00:00+00:00" % (minute % 24), zone)
    assert counted == [], f"{len(counted)} reads although the caller passed the zone"


def test_the_zone_is_still_the_one_that_is_set(counted):
    moment = db_reader._local_dt("2026-03-14T12:00:00+00:00")
    assert moment.utcoffset().total_seconds() == 3600, moment      # Europe/Rome in March, CET


def test_a_change_is_never_served_stale(counted):
    """Whoever writes it — this process, the poller, a restore straight into the table — the next
    render must show the new zone. A cache with a lifetime cannot promise that; this does."""
    assert db_reader._local_dt("2026-03-14T12:00:00+00:00").utcoffset().total_seconds() == 3600
    written = db_reader._conn_rw()
    written.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','UTC')")
    written.commit()
    after = db_reader._local_dt("2026-03-14T12:00:00+00:00")
    assert after.utcoffset().total_seconds() == 0, (
        f"the page still shows the old zone after the setting was changed behind the API: {after}"
    )
