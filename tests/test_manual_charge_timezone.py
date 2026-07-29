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

⚠️ That marker alone turned out to be half the story, and v2.12.1 shipped with only half. It is
idempotent but it freezes the zone in force at the first start after the update — and installing, then
choosing your zone, is the ordinary order of events. See the second half of this file.
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
    """A real schema in a throwaway file — never the ambient DB, which would mask the dependency.

    Each charge is (started, ended, location_type) or (started, ended, location_type, manual_entry).
    Left out, the marker follows the location_type — which is what these fixtures have always meant
    by 'MANUAL': a row somebody typed. The two meanings of that word are pulled apart in the last
    section of this file, where a MEASURED charge wears the same tag."""
    path = str(tmp_path / "web.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'VIN123')")
    for row in charges:
        started, ended, loc = row[:3]
        manual = row[3] if len(row) > 3 else (1 if loc == "MANUAL" else 0)
        con.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, "
                    "location_type, manual_entry) VALUES (1, ?, ?, 10.0, ?, ?)",
                    (started, ended, loc, manual))
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
    _settings(monkeypatch, {"timezone": "Europe/Rome"})   # nothing converts until one is chosen
    assert db_reader.repair_manual_charge_timezones() == 2
    rows = _rows(path)
    assert rows[0][0] == "2025-11-03T19:30:00+00:00"
    assert rows[0][1] == "2025-11-03T23:37:00+00:00"
    assert rows[1][0] == "2026-01-15T10:00:00+00:00" and rows[1][1] is None


def test_repair_is_idempotent(tmp_path, monkeypatch):
    """The whole point of marking the work in the data: a second pass must move nothing. Run twice and
    the timestamps have to be identical, or every restart would push the charges further out."""
    path = _db(tmp_path, monkeypatch, [("2025-11-03T21:30:00", None, "MANUAL")])
    _settings(monkeypatch, {"timezone": "Europe/Rome"})
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


# ── the flaw v2.12.1 shipped, and what replaced it ───────────────────────────────
#
# v2.12.1 marked a row "done" the moment it carried a zone. Idempotent, and wrong: it freezes
# whatever zone was configured at the first start after the update. Installing and THEN choosing
# your zone is the normal order, so an install could be stamped as UTC and never revisit itself.
# @ghuaywen-ai's 150 charges were still 8 hours out while the marker said converted.

PLUS8 = timezone(timedelta(hours=8))


def _settings(monkeypatch, store):
    monkeypatch.setattr(db_reader, "get_setting", lambda k, d="": store.get(k, d))
    monkeypatch.setattr(db_reader, "set_setting", lambda k, v: store.__setitem__(k, str(v)))


def test_nothing_is_converted_before_a_zone_has_been_chosen(tmp_path, monkeypatch):
    """The heart of it: with no answer to "whose clock is this?", waiting beats guessing and
    marking the guess as settled."""
    path = _db(tmp_path, monkeypatch, [("2026-07-21T10:40:00", None, "MANUAL")])
    _settings(monkeypatch, {})                       # no timezone setting at all
    assert db_reader.repair_manual_charge_timezones() == 0
    assert _rows(path)[0][0] == "2026-07-21T10:40:00"   # still naive → still repairable later


def test_it_converts_once_the_zone_is_chosen(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch, [("2026-07-21T10:40:00", None, "MANUAL")])
    store = {"timezone": "Asia/Kuala_Lumpur"}
    _settings(monkeypatch, store)
    monkeypatch.setattr(db_reader, "_local_tz", lambda: PLUS8)
    assert db_reader.repair_manual_charge_timezones() == 1
    assert _rows(path)[0][0] == "2026-07-21T02:40:00+00:00"     # 10:40 in +08:00
    assert store[db_reader.TZ_REPAIR_ZONE_KEY] == "Asia/Kuala_Lumpur"


def test_a_zone_chosen_afterwards_re_anchors_what_the_wrong_one_converted(tmp_path, monkeypatch):
    """ghuaywen-ai's case exactly: converted under UTC, zone set to Kuala Lumpur later. His 10:40
    must come back to 10:40, not stay at 18:40."""
    path = _db(tmp_path, monkeypatch, [("2026-07-21T10:40:00", None, "MANUAL")])
    store = {"timezone": "UTC"}
    _settings(monkeypatch, store)
    monkeypatch.setattr(db_reader, "_local_tz", lambda: timezone.utc)
    db_reader.repair_manual_charge_timezones()
    assert _rows(path)[0][0] == "2026-07-21T10:40:00+00:00"     # stamped, and displayed 8h late

    store["timezone"] = "Asia/Kuala_Lumpur"
    monkeypatch.setattr(db_reader, "_local_tz", lambda: PLUS8)
    monkeypatch.setattr(db_reader, "_resolve_tz", lambda name: timezone.utc)
    assert db_reader.repair_manual_charge_timezones() == 1
    assert _rows(path)[0][0] == "2026-07-21T02:40:00+00:00"     # reads 10:40 again, in his zone


def test_a_charge_entered_after_the_conversion_is_never_re_anchored(tmp_path, monkeypatch):
    """Moving to another country doesn't change when you plugged in. Rows written correctly under
    the zone of the day must stay where they are, which is what the id bound protects."""
    path = _db(tmp_path, monkeypatch, [("2026-07-21T10:40:00", None, "MANUAL")])
    store = {"timezone": "UTC"}
    _settings(monkeypatch, store)
    monkeypatch.setattr(db_reader, "_local_tz", lambda: timezone.utc)
    db_reader.repair_manual_charge_timezones()

    import sqlite3 as _s
    con = _s.connect(path)
    con.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, "
                "location_type) VALUES (1, '2026-07-25T09:00:00+00:00', NULL, 10.0, 'MANUAL')")
    con.commit(); con.close()

    store["timezone"] = "Asia/Kuala_Lumpur"
    monkeypatch.setattr(db_reader, "_local_tz", lambda: PLUS8)
    monkeypatch.setattr(db_reader, "_resolve_tz", lambda name: timezone.utc)
    db_reader.repair_manual_charge_timezones()
    assert _rows(path)[1][0] == "2026-07-25T09:00:00+00:00"     # untouched


# ── #188: 'MANUAL' means two things, and only one of them belongs here ───────────
#
# location_type='MANUAL' is what add_manual_charge writes on a typed-in charge — AND what the badge
# writes on a MEASURED one when the user picks "Manual" to type the price of a public charge Mate
# can't tariff. This repair asks "is this wall-clock text somebody typed?", and until v2.15.0 it
# asked with the wider of the two, so a measured session was in scope. The first pass leaves it be
# (its timestamp already carries a zone), which is why nobody saw this; a later zone CHANGE puts it
# through the re-anchoring branch and rewrites the timestamp the CAR recorded.
#
# Measured while building #188: a charge recorded at 07:54 UTC came out at 13:54 after a move from
# Europe/Rome to America/New_York. The marker the edit form needed, manual_entry, is also the right
# question to ask here.

def test_a_measured_charge_tagged_manual_for_its_price_is_out_of_scope(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch, [("2026-07-08T07:54:12+00:00", None, "MANUAL", 0)])
    store = {"timezone": "UTC"}
    _settings(monkeypatch, store)
    monkeypatch.setattr(db_reader, "_local_tz", lambda: timezone.utc)
    assert db_reader.repair_manual_charge_timezones() == 0
    assert _rows(path)[0][0] == "2026-07-08T07:54:12+00:00"


def test_a_zone_change_does_not_move_the_car_s_own_timestamp(tmp_path, monkeypatch):
    """The failure this fixes: the typed row is re-anchored, the measured one stays put."""
    path = _db(tmp_path, monkeypatch, [
        ("2026-07-21T10:40:00", None, "MANUAL"),                  # typed: wall clock, no zone
        ("2026-07-08T07:54:12+00:00", None, "MANUAL", 0),         # measured, tagged for its price
    ])
    store = {"timezone": "UTC"}
    _settings(monkeypatch, store)
    monkeypatch.setattr(db_reader, "_local_tz", lambda: timezone.utc)
    db_reader.repair_manual_charge_timezones()

    store["timezone"] = "Asia/Kuala_Lumpur"
    monkeypatch.setattr(db_reader, "_local_tz", lambda: PLUS8)
    monkeypatch.setattr(db_reader, "_resolve_tz", lambda name: timezone.utc)
    assert db_reader.repair_manual_charge_timezones() == 1        # the typed one, and only it
    rows = _rows(path)
    assert rows[0][0] == "2026-07-21T02:40:00+00:00"              # re-anchored, reads 10:40 again
    assert rows[1][0] == "2026-07-08T07:54:12+00:00"              # untouched, to the second
