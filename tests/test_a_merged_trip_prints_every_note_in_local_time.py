"""A joined trip prints the times of its later pieces in the local zone, like its header — #279.

@pdifeo, 15/09/26, on the issue we had closed with v3.15.10: *«the time on joined notes is in UTC
Time and not CEST. The time on the top is right»*. His screenshots: the header said **05:53**, the
two notes below it **04:04 → 04:28** and **04:30 → 04:37** — two hours early, the CEST offset.

The header and the notes are printed by the same `[11:16]` slice in trip_detail.html. The slice only
shows local time when the string was localized first: `get_trip_detail` did that for the trip's own
`started_at`/`ended_at`, but the later pieces' notes (added in v3.15.10 by `_trip_group_stats`)
reached the template as the raw UTC strings the poller stores.

The trips below are stored the way the poller stores them (`datetime.now(timezone.utc).isoformat()`)
and the zone is pinned in settings, so the result does not depend on the machine running the test.
"""
import pytest

import db as D
import db_reader


def _setup(tmp_path, monkeypatch, zone):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(67.0)
    pdb.ensure_vehicle("LVIN0000000000001", "B10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.set_setting("timezone", zone)
    return pdb


def _trip(pdb, started_at, ended_at, note, merged_into_id=None):
    cur = pdb._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " efficiency_kwh_100km, note, merged_into_id) VALUES (1,?,?,10.0,80.0,75.0,15.0,?,?)",
        (started_at, ended_at, note, merged_into_id))
    pdb._conn.commit()
    return cur.lastrowid


def _pdifeo_morning(pdb, day="2026-09-15"):
    """His three pieces, in UTC: 03:53, then 04:04 → 04:28 and 04:30 → 04:37."""
    parent = _trip(pdb, f"{day}T03:53:10.512344+00:00", f"{day}T04:02:41.100000+00:00", "first")
    _trip(pdb, f"{day}T04:04:05.000001+00:00", f"{day}T04:28:59.999999+00:00", "second", parent)
    _trip(pdb, f"{day}T04:30:00+00:00", f"{day}T04:37:30+00:00", "third", parent)
    return parent


def _printed(detail):
    """What trip_detail.html prints: `trip.started_at[11:16]` in the header and
    `segment.started_at[11:16] → segment.ended_at[11:16]` above each later note."""
    return (detail["started_at"][11:16],
            [(s["started_at"][11:16], s["ended_at"][11:16], s["note"])
             for s in detail["additional_segment_notes"]])


def test_his_morning_prints_every_note_in_his_zone(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch, "Europe/Rome")
    parent = _pdifeo_morning(pdb)
    assert _printed(db_reader.get_trip_detail(parent)) == (
        "05:53", [("06:04", "06:28", "second"), ("06:30", "06:37", "third")])


@pytest.mark.parametrize("zone, day, expected", [
    # winter in Rome: one hour, not two — the offset is the one in force on that date
    ("Europe/Rome", "2026-01-15", ("04:53", [("05:04", "05:28", "second"), ("05:30", "05:37", "third")])),
    # west of Greenwich the notes go back to the previous evening, the header with them
    ("America/New_York", "2026-09-15", ("23:53", [("00:04", "00:28", "second"), ("00:30", "00:37", "third")])),
    # a zone that IS UTC prints the stored times unchanged
    ("UTC", "2026-09-15", ("03:53", [("04:04", "04:28", "second"), ("04:30", "04:37", "third")])),
])
def test_the_notes_follow_the_same_zone_as_the_header(tmp_path, monkeypatch, zone, day, expected):
    pdb = _setup(tmp_path, monkeypatch, zone)
    parent = _pdifeo_morning(pdb, day)
    assert _printed(db_reader.get_trip_detail(parent)) == expected


def test_the_page_prints_his_times(tmp_path, monkeypatch):
    """The same morning through the real route and the real template, as he opens it."""
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    from starlette.testclient import TestClient
    import main

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    pdb = _setup(tmp_path, monkeypatch, "Europe/Rome")
    db_reader.set_setting("setup_complete", "1")
    parent = _pdifeo_morning(pdb)

    html = TestClient(main.app).get(f"/trips/{parent}").text

    assert "06:04 &rarr; 06:28" in html
    assert "06:30 &rarr; 06:37" in html
    assert "04:04" not in html and "04:30" not in html
