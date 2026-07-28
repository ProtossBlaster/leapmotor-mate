"""Hand-entered charges are stored in UTC, like everything else (#181 ghuaywen-ai).

The renderer (`db_reader._local_dt`) reads a zone-less timestamp AS UTC and converts it to the display
zone. Both hand-entry paths — the Charges form and the CSV import — used to save the wall-clock text the
user typed, with no zone at all. So every manually added charge came back on screen pushed forward by
the user's whole offset: +7 h for the reporter, on all 150+ of his imported rows, and quietly wrong for
everyone outside UTC ever since the feature shipped.

The repair marks itself in the DATA rather than in a settings flag: a row that already carries a zone is
skipped. That is what makes it safe to run at every startup — and it is deliberately unlike the other
one-time repairs in this project, each of which trusts a flag that, if it ever goes missing, re-runs the
migration over already-migrated rows.
"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "poller")
import db as poller_db          # noqa: E402
import db_reader                # noqa: E402

PLUS2 = timezone(timedelta(hours=2))


def _db(tmp_path, monkeypatch, charges):
    """A real schema in a throwaway file — never the ambient DB, which would mask the dependency."""
    path = str(tmp_path / "web.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'VIN123')")
    for started, ended, loc in charges:
        con.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, "
                    "location_type) VALUES (1, ?, ?, 10.0, ?)", (started, ended, loc))
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_local_tz", lambda: PLUS2)
    return path


def _rows(path):
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    out = [(r["started_at"], r["ended_at"], r["location_type"])
           for r in con.execute("SELECT * FROM charges ORDER BY id")]
    con.close()
    return out


# ── the converter ────────────────────────────────────────────────────────────────
def test_naive_time_is_anchored_to_the_display_zone():
    assert db_reader.local_to_utc_iso("2025-11-03T21:30:00", PLUS2) == "2025-11-03T19:30:00+00:00"


def test_already_zoned_value_is_returned_untouched():
    # This is what makes the repair idempotent — the second run finds nothing left to do.
    zoned = "2025-11-03T19:30:00+00:00"
    assert db_reader.local_to_utc_iso(zoned, PLUS2) == zoned


def test_unparseable_and_empty_values_survive():
    assert db_reader.local_to_utc_iso("", PLUS2) == ""
    assert db_reader.local_to_utc_iso(None, PLUS2) is None
    assert db_reader.local_to_utc_iso("not a date", PLUS2) == "not a date"


def test_offset_used_is_the_one_in_force_on_that_date():
    """A blanket 'add today's offset' would fix summer and break winter. ZoneInfo resolves the offset
    per instant, so the same wall clock in January and in July converts differently."""
    zi = pytest.importorskip("zoneinfo", reason="tzdata not available in this env")
    try:
        rome = zi.ZoneInfo("Europe/Rome")
    except Exception:                                   # noqa: BLE001 — no tzdata in the image
        pytest.skip("Europe/Rome not in tzdata here")
    winter = db_reader.local_to_utc_iso("2026-01-15T12:00:00", rome)
    summer = db_reader.local_to_utc_iso("2026-07-15T12:00:00", rome)
    assert winter == "2026-01-15T11:00:00+00:00"        # CET  = +01:00
    assert summer == "2026-07-15T10:00:00+00:00"        # CEST = +02:00


# ── the repair ───────────────────────────────────────────────────────────────────
def test_repair_moves_manual_rows_to_utc(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch, [
        ("2025-11-03T21:30:00", "2025-11-04T01:37:00", "MANUAL"),
        ("2026-01-15T12:00:00", None, "MANUAL"),
    ])
    assert db_reader.repair_manual_charge_timezones() == 2
    rows = _rows(path)
    assert rows[0][0] == "2025-11-03T19:30:00+00:00"
    assert rows[0][1] == "2025-11-03T23:37:00+00:00"
    assert rows[1][0] == "2026-01-15T10:00:00+00:00" and rows[1][1] is None


def test_repair_is_idempotent(tmp_path, monkeypatch):
    """The whole point of marking the work in the data: a second pass must move nothing. Run twice and
    the timestamps have to be identical, or every restart would push the charges further out."""
    path = _db(tmp_path, monkeypatch, [("2025-11-03T21:30:00", None, "MANUAL")])
    assert db_reader.repair_manual_charge_timezones() == 1
    after_first = _rows(path)
    assert db_reader.repair_manual_charge_timezones() == 0
    assert _rows(path) == after_first


def test_repair_never_touches_the_pollers_own_rows(tmp_path, monkeypatch):
    # The poller has always written UTC. Those rows carry no 'MANUAL' marker and must be left alone
    # even in the (impossible on its path) case of a zone-less value.
    path = _db(tmp_path, monkeypatch, [
        ("2025-11-03T21:30:00+00:00", None, "HOME"),
        ("2025-11-04T08:00:00", None, "FAST"),
    ])
    assert db_reader.repair_manual_charge_timezones() == 0
    assert [r[0] for r in _rows(path)] == ["2025-11-03T21:30:00+00:00", "2025-11-04T08:00:00"]
