"""#247 (@Ng-EY) — the 🧭 summary of a MERGED trip must describe the merged journey.

Merge A→B with B→C and the page shows one journey, A→C: get_trip_detail resolves a child to its
parent and composes the group (_trip_group_stats), so the map, the arrival time and the distance
all reach C. The summary button used to read the parent's own stored row instead and write "A→B" —
the trip's own note contradicting the trip it is attached to, on the same screen.

The stored rows must stay untouched either way: a merge is display math, and unmerging has to
restore the originals exactly. Only what the note READS changes.
"""
import pytest

import db as D
import db_reader

# A, B, C — three places, one per latitude, so the fake geocoder can name them apart.
A, B, C = (45.0, 9.0), (45.1, 9.1), (45.2, 9.2)
_PLACES = {45.0: "Point A", 45.1: "Point B", 45.2: "Point C"}


def _hhmm(iso: str) -> str:
    """The note formats in local time, so a literal UTC hour would fail under any other tz."""
    return db_reader._local_dt(iso).strftime("%H:%M")


@pytest.fixture
def pdb(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    import geocode
    monkeypatch.setattr(geocode, "reverse_geocode",
                        lambda lat, lon, provider, api_key: _PLACES.get(round(lat, 1)))
    return database


def _add_trip(pdb, started, ended, start, end, temp_start=None, temp_end=None):
    pdb._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon,"
        " outside_temp_start_c, outside_temp_end_c) VALUES (1,?,?,?,?,?,?,?,?)",
        (started, ended, start[0], start[1], end[0], end[1], temp_start, temp_end))
    pdb._conn.commit()
    return db_reader._get().execute("SELECT MAX(id) AS id FROM trips").fetchone()["id"]


def _merged_pair(pdb):
    """A→B (parent) + B→C (child), joined the way the merge UI joins them."""
    parent = _add_trip(pdb, "2026-08-12T10:00:00+00:00", "2026-08-12T10:30:00+00:00", A, B, 20.0, 21.0)
    child = _add_trip(pdb, "2026-08-12T10:34:00+00:00", "2026-08-12T11:10:00+00:00", B, C, 21.0, 25.0)
    conn = db_reader._conn_rw()
    conn.execute("UPDATE trips SET merged_into_id=? WHERE id=?", (parent, child))
    conn.commit()
    return parent, child


def test_the_summary_of_a_merged_trip_reaches_the_final_destination(pdb):
    parent, _child = _merged_pair(pdb)

    note = db_reader.generate_trip_auto_note(parent)

    assert "Point A" in note                       # where the journey began
    assert "Point C" in note                       # where it ENDED — not the parent segment's B
    assert "Point B" not in note                   # the midpoint is not an endpoint
    assert _hhmm("2026-08-12T11:10:00+00:00") in note   # arrival at C, not 10:30 at B


def test_the_summary_takes_the_end_temperature_from_the_last_segment(pdb):
    parent, _child = _merged_pair(pdb)

    note = db_reader.generate_trip_auto_note(parent)

    assert "25" in note        # the temperature at C
    assert "21" not in note    # not the one recorded arriving at B


def test_pressing_it_on_a_child_writes_the_group_note_onto_the_parent(pdb):
    """The page resolves a child to its parent, so the note it reads back is the parent's."""
    parent, child = _merged_pair(pdb)

    note = db_reader.generate_trip_auto_note(child)

    assert "Point A" in note and "Point C" in note
    rows = {r["id"]: r["note"] for r in
            db_reader._get().execute("SELECT id, note FROM trips").fetchall()}
    assert rows[parent] == note
    assert rows[child] is None


def test_the_merge_stays_reversible_the_rows_are_untouched(pdb):
    parent, child = _merged_pair(pdb)

    db_reader.generate_trip_auto_note(parent)

    kid = db_reader._get().execute("SELECT * FROM trips WHERE id=?", (child,)).fetchone()
    assert (kid["end_lat"], kid["end_lon"]) == C          # the child still owns its own endpoints
    assert kid["merged_into_id"] == parent
    mum = db_reader._get().execute("SELECT * FROM trips WHERE id=?", (parent,)).fetchone()
    assert (mum["end_lat"], mum["end_lon"]) == B          # and the parent still ends at B


def test_an_ordinary_unmerged_trip_is_unchanged(pdb):
    tid = _add_trip(pdb, "2026-08-12T10:00:00+00:00", "2026-08-12T10:30:00+00:00", A, B, 20.0, 21.0)

    note = db_reader.generate_trip_auto_note(tid)

    assert "Point A" in note and "Point B" in note
    assert _hhmm("2026-08-12T10:30:00+00:00") in note
