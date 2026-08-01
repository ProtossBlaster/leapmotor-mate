"""Where you filled up, written by Mate — beta discussion #14, @gm27271.

"Add GPS coordinates of the gas station registered during the refueling timestamp… then users do
not need to enter any notes, it will autodetect where the gas was added."

The machinery for that already existed and was simply not wired to this page: the 🧭 button writes
a reverse-geocoded address into a trip's or a charge's note. A refuel has the same shape — a moment
in time, and a place the car was standing — so it now has the same button.

The honest part is the guard. A refuel's timestamp is not always the moment fuel went in: on a
detection it is when the NEW level was first seen, which on a car that fills up and drives home is
the next time it woke. So the address is only written when the car actually reported a position
near that time; otherwise the note is left alone rather than naming a place the car had left.
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import geocode


def _at(mins):
    return (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat()


def _setup(tmp_path, monkeypatch, *, position_minutes_before=2):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    ts = _at(60)
    if position_minutes_before is not None:
        pdb._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude) VALUES (1,?,?,?)",
            (_at(60 + position_minutes_before), 45.4642, 9.1900))
    pdb._conn.commit()
    pid = db_reader.add_fuel_purchase(ts, 34.5, price_per_l=1.75)
    monkeypatch.setattr(geocode, "reverse_geocode",
                        lambda lat, lon, provider="", api_key=None: "Via Roma 1, Milano")
    return pdb, pid


def _note(pdb, pid):
    return pdb._conn.execute("SELECT note FROM fuel_purchases WHERE id=?", (pid,)).fetchone()["note"]


# ── the feature ───────────────────────────────────────────────────────────────
def test_the_address_of_the_pump_is_written_into_the_note(tmp_path, monkeypatch):
    pdb, pid = _setup(tmp_path, monkeypatch)
    out = db_reader.generate_fuel_auto_note(pid)
    assert "Via Roma 1, Milano" in (out or "")
    assert "Via Roma 1, Milano" in (_note(pdb, pid) or "")


# ── and the guard: no position near it → say nothing ──────────────────────────
def test_no_position_near_the_refuel_leaves_the_note_alone(tmp_path, monkeypatch):
    """The car filled up and drove off before reporting: the nearest fix is an hour away and
    belongs somewhere else entirely. Naming it would be worse than naming nothing."""
    pdb, pid = _setup(tmp_path, monkeypatch, position_minutes_before=90)
    assert db_reader.generate_fuel_auto_note(pid) is None
    assert not (_note(pdb, pid) or "")


def test_a_note_you_typed_is_never_clobbered_by_the_automatic_path(tmp_path, monkeypatch):
    pdb, pid = _setup(tmp_path, monkeypatch)
    pdb._conn.execute("UPDATE fuel_purchases SET note='the cheap one on the ring road' WHERE id=?",
                      (pid,))
    pdb._conn.commit()
    db_reader.generate_fuel_auto_note(pid, only_if_note_empty=True)
    assert _note(pdb, pid) == "the cheap one on the ring road"


def test_the_button_always_overwrites(tmp_path, monkeypatch):
    """The 🧭 button is a deliberate click — the page asks first when there is something to lose."""
    pdb, pid = _setup(tmp_path, monkeypatch)
    pdb._conn.execute("UPDATE fuel_purchases SET note='old' WHERE id=?", (pid,))
    pdb._conn.commit()
    db_reader.generate_fuel_auto_note(pid)
    assert "Via Roma 1, Milano" in _note(pdb, pid)


def test_an_unknown_refuel_id_does_not_raise(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert db_reader.generate_fuel_auto_note(999999) is None
