"""The 🧭 note of a merged charge must describe the whole session (#247, @Ng-EY).

He reported it first for trips — *"the generate summary button only generate to midpoint of merge
trip instead of the merged destination"* — and it was fixed in v3.11.2. Three days later, on the
same issue rather than a new one:

> *"Instead of opening another thread, I think we got the same issue on the user note on merged
> charges."*

He is right, and it is the identical shape: `generate_charge_auto_note` reads the charge's OWN row
while every other reader composes the merge group. So on a plug-in the car reported in pieces — the
normal case, since it declares the cable gone at each pause — the note stopped at the first piece's
end while the card above it showed the whole session.

Same fix as the trip's, and the same two faces: the endpoints come from the composed group, and the
note is written to the PARENT, which is the row the page reads it back from. The stored rows stay
untouched, because a merge is display math and has to stay reversible.
"""
import pytest


def _merged_charge(tmp_path, monkeypatch):
    """One plug-in the car split in two: 21:00→22:10 and 22:40→23:50, joined."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh,"
              " start_soc, end_soc, location_type, charge_type, merged_into_id)"
              " VALUES (1,1,'2026-08-14T21:00:00+00:00','2026-08-14T22:10:00+00:00',10.0,"
              "40.0,58.0,'HOME','AC',NULL)")
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh,"
              " start_soc, end_soc, location_type, charge_type, merged_into_id)"
              " VALUES (2,1,'2026-08-14T22:40:00+00:00','2026-08-14T23:50:00+00:00',9.0,"
              "58.0,74.0,'HOME','AC',1)")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','UTC')")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def test_the_note_reaches_the_end_of_the_whole_session(tmp_path, monkeypatch):
    """23:50 is where the plug-in ended; 22:10 is where the first piece stopped."""
    d = _merged_charge(tmp_path, monkeypatch)
    note = d.generate_charge_auto_note(1) or ""
    assert "23:50" in note, note
    assert "22:10" not in note, f"the note stops at the pause: {note}"


def test_it_still_starts_where_the_session_started(tmp_path, monkeypatch):
    d = _merged_charge(tmp_path, monkeypatch)
    assert "21:00" in (d.generate_charge_auto_note(1) or "")


def test_pressing_it_on_the_child_describes_the_group_too(tmp_path, monkeypatch):
    """The second face of the same defect: the card shows the whole session whichever piece you
    are looking at, so the button must not describe one segment alone."""
    d = _merged_charge(tmp_path, monkeypatch)
    note = d.generate_charge_auto_note(2) or ""
    assert "21:00" in note and "23:50" in note, note


def test_the_note_lands_on_the_parent_row(tmp_path, monkeypatch):
    """Written to the row the page reads it back from — otherwise it is generated and invisible."""
    import sqlite3
    d = _merged_charge(tmp_path, monkeypatch)
    d.generate_charge_auto_note(2)
    con = sqlite3.connect(d.DB_PATH)
    notes = dict(con.execute("SELECT id, COALESCE(note,'') FROM charges").fetchall())
    assert notes[1], "the parent has no note"
    assert not notes[2], f"the child kept a note of its own: {notes[2]!r}"


def test_an_unmerged_charge_is_unchanged(tmp_path, monkeypatch):
    """The ordinary case must not learn anything new."""
    import db as D
    d = _merged_charge(tmp_path, monkeypatch)
    pdb = D.Database(d.DB_PATH)
    pdb._conn.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at,"
                      " energy_added_kwh, start_soc, end_soc, location_type, charge_type)"
                      " VALUES (3,1,'2026-08-16T08:00:00+00:00','2026-08-16T09:30:00+00:00',"
                      "12.0,30.0,55.0,'HOME','AC')")
    pdb._conn.commit()
    pdb._conn.close()
    note = d.generate_charge_auto_note(3) or ""
    assert "08:00" in note and "09:30" in note, note
