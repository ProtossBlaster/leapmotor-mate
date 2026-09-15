"""User notes + manual driving tags (#107): a free-text note on charges and trips, plus manual
drive_mode / One-Pedal tags on trips (the Leapmotor cloud doesn't expose those, so the user sets
them). Pure poller.db + db_reader on a tmp DB → CI-safe."""
import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(67.0)
    pdb.ensure_vehicle("LVIN0000000000001", "B10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _insert_trip(pdb, **over):
    cols = {
        "vehicle_id": 1,
        "started_at": "2026-05-01T08:00:00",
        "ended_at": "2026-05-01T08:30:00",
        "distance_km": 20.0,
        "start_soc": 80.0,
        "end_soc": 70.0,
        "efficiency_kwh_100km": 15.0,
    }
    cols.update(over)
    keys = ",".join(cols)
    ph = ",".join("?" * len(cols))
    cur = pdb._conn.execute(f"INSERT INTO trips ({keys}) VALUES ({ph})", tuple(cols.values()))
    pdb._conn.commit()
    return cur.lastrowid


# ── migration: the columns exist on a fresh DB ───────────────────────────────

def test_note_columns_exist(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    ccols = {r[1] for r in pdb._conn.execute("PRAGMA table_info(charges)").fetchall()}
    tcols = {r[1] for r in pdb._conn.execute("PRAGMA table_info(trips)").fetchall()}
    assert "note" in ccols
    assert {"note", "drive_mode", "one_pedal"} <= tcols


# ── charge note ──────────────────────────────────────────────────────────────

def test_charge_note_save_and_read(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-05-01T12:00:00", 24.5, cost=6.0, charge_type="AC")
    db_reader.save_charge_note(cid, "  Shaded bay, reliable Ionity  ")
    row = dict(db_reader._get().execute("SELECT note FROM charges WHERE id=?", (cid,)).fetchone())
    assert row["note"] == "Shaded bay, reliable Ionity"   # trimmed


def test_charge_note_empty_clears_to_null(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-05-01T12:00:00", 10.0, charge_type="DC")
    db_reader.save_charge_note(cid, "something")
    db_reader.save_charge_note(cid, "   ")               # whitespace → clear
    row = db_reader._get().execute("SELECT note FROM charges WHERE id=?", (cid,)).fetchone()
    assert row["note"] is None


def test_charge_note_truncated_to_1000(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-05-01T12:00:00", 10.0, charge_type="DC")
    db_reader.save_charge_note(cid, "x" * 5000)
    row = db_reader._get().execute("SELECT note FROM charges WHERE id=?", (cid,)).fetchone()
    assert len(row["note"]) == 1000


# ── trip note + driving tags ─────────────────────────────────────────────────

def test_trip_note_and_tags_roundtrip(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    tid = _insert_trip(pdb)
    db_reader.save_trip_note(tid, "Motorway, headwind", drive_mode="sport", one_pedal=1)
    detail = db_reader.get_trip_detail(tid)
    assert detail["note"] == "Motorway, headwind"
    assert detail["drive_mode"] == "sport"
    assert detail["one_pedal"] == 1


def test_trip_one_pedal_off_is_preserved(tmp_path, monkeypatch):
    # 0 is falsy but a valid, distinct value ("explicitly off") — must not collapse to "not set".
    pdb = _setup(tmp_path, monkeypatch)
    tid = _insert_trip(pdb)
    db_reader.save_trip_note(tid, "", drive_mode="comfort", one_pedal=0)
    detail = db_reader.get_trip_detail(tid)
    assert detail["one_pedal"] == 0
    assert detail["drive_mode"] == "comfort"
    assert detail["note"] is None


def test_trip_invalid_tags_cleared(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    tid = _insert_trip(pdb)
    db_reader.save_trip_note(tid, "note", drive_mode="ludicrous", one_pedal=7)
    detail = db_reader.get_trip_detail(tid)
    assert detail["drive_mode"] is None     # not one of comfort/normal/sport
    assert detail["one_pedal"] is None      # not 0/1
    assert detail["note"] == "note"


def test_trip_note_saved_on_parent(tmp_path, monkeypatch):
    # The detail page resolves a merged child to its parent; a note edited there must land on
    # (and read back from) the parent trip.
    pdb = _setup(tmp_path, monkeypatch)
    parent = _insert_trip(pdb)
    child = _insert_trip(pdb, started_at="2026-05-01T08:35:00", merged_into_id=parent)
    db_reader.save_trip_note(parent, "grouped note", drive_mode="normal", one_pedal=1)
    # Opening the child shows the parent group → the note comes through.
    detail = db_reader.get_trip_detail(child)
    assert detail["note"] == "grouped note"
    assert detail["drive_mode"] == "normal"


def test_merged_trip_keeps_later_segment_notes_visible_and_reversible(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    db_reader.set_setting("timezone", "Europe/Rome")
    parent = _insert_trip(pdb, note="First automatic note")
    child = _insert_trip(
        pdb,
        started_at="2026-05-01T08:35:00",
        ended_at="2026-05-01T09:00:00",
        note="Second automatic note",
        merged_into_id=parent,
    )

    detail = db_reader.get_trip_detail(parent)
    assert detail["note"] == "First automatic note"
    # Local time, like the header: this asserted the raw UTC string until #279 showed it on screen
    # two hours early — see test_a_merged_trip_prints_every_note_in_local_time.py.
    assert detail["additional_segment_notes"] == [
        {
            "id": child,
            "started_at": "2026-05-01T10:35:00+02:00",
            "ended_at": "2026-05-01T11:00:00+02:00",
            "note": "Second automatic note",
        }
    ]

    db_reader.unmerge_trip(parent)
    notes = dict(pdb._conn.execute("SELECT id, note FROM trips ORDER BY id").fetchall())
    assert notes == {parent: "First automatic note", child: "Second automatic note"}
