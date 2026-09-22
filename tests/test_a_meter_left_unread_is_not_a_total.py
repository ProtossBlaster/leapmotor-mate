"""A wallbox total built while nobody was reading the meter is short by an unknown amount (#295).

The third way a counter lies, after running away (#46) and standing still (#215): it is fine, and
we did not look at it. @gm27271's charge was open for twelve hours and read for nineteen minutes of
them — the total it ended on, 7.69 kWh, was never a measurement of that charge.

Watching the meter through an outage (`sample_wallbox_meter`) closes the usual door. Three stay
open, and all three are silent: Home Assistant itself unreachable while the car charges on, a
poller restarted mid-charge that cannot resume the charge because the cloud is dark at that moment,
and a container simply stopped for a few hours. So the time a charge spends OPEN AT THE WALLBOX
with no reading taken is counted, and past the threshold the total is dropped — the same answer
`finalize_charge` already gives to a runaway and to a frozen counter, and for the same reason: the
DC figure is at least complete.

⚠️ The test is a MEASUREMENT — our own clock against our own reads — and never a comparison with
the charge's DC energy. `poller/db.py` says why in full: that figure is ΔSoC × a battery capacity
the owner types in, and a car with the capacity wrong would have its perfectly good meter thrown
away on every charge. The weaker number must not be allowed to discredit the stronger one.
"""
import types
from datetime import datetime, timedelta, timezone

import db as D
import pytest


def _at(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _open_charge(db, soc=42.1):
    return db.create_charge(1, types.SimpleNamespace(soc=soc, latitude=1.0, longitude=2.0))


def _ac(db, cid):
    return db._conn.execute("SELECT ac_energy_kwh FROM charges WHERE id=?", (cid,)).fetchone()[0]


def _dark(db, cid):
    return db._conn.execute("SELECT wb_dark_min FROM charges WHERE id=?", (cid,)).fetchone()[0]


# ── the unread time is counted ───────────────────────────────────────────────

def test_a_poll_with_no_reading_adds_its_minutes(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    cid = _open_charge(db)
    db.set_charge_wallbox_start(cid, 11.78)
    db.note_wallbox_unread(cid, 0.5)          # one 30-second poll with no answer from HA
    db.note_wallbox_unread(cid, 0.5)
    assert _dark(db, cid) == 1.0


def test_a_reading_does_not_erase_the_time_already_lost(tmp_path):
    """Unlike the #215 stall — which a real rise clears, because the counter proved itself alive —
    minutes nobody measured stay lost: the meter coming back says nothing about what it missed."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = _open_charge(db)
    db.set_charge_wallbox_start(cid, 11.78)
    db.note_wallbox_unread(cid, 12.0)
    db.accumulate_wallbox_energy(cid, 13.20)
    assert _dark(db, cid) == 12.0


# ── and it decides whether the total may be published ────────────────────────

def test_finalize_drops_a_total_measured_through_a_long_blind_spell(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    cid = _open_charge(db)
    db._conn.execute("UPDATE charges SET started_at=?, ac_energy_kwh=7.69, wb_dark_min=86 "
                     "WHERE id=?", (_at(hours=12), cid))
    db._conn.commit()
    db.finalize_charge(cid, types.SimpleNamespace(soc=77.4, latitude=1.0, longitude=2.0),
                       max_power_kw=6.1)
    assert _ac(db, cid) is None, "kept DC billing instead of a total nobody measured"


def test_finalize_keeps_a_total_after_a_hiccup(tmp_path):
    """A couple of missed polls is a hiccup, not a blind spell — dropping the meter figure there
    would change what people are billed for no good reason."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = _open_charge(db)
    db._conn.execute("UPDATE charges SET started_at=?, ac_energy_kwh=11.2, wb_dark_min=2 "
                     "WHERE id=?", (_at(hours=3), cid))
    db._conn.commit()
    db.finalize_charge(cid, types.SimpleNamespace(soc=84.0, latitude=1.0, longitude=2.0),
                       max_power_kw=3.2)
    assert _ac(db, cid) == 11.2


def test_a_charge_with_no_meter_at_all_is_untouched(tmp_path):
    """No wallbox configured: there is no AC figure to drop and no blind spell to report."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = _open_charge(db)
    db._conn.execute("UPDATE charges SET started_at=? WHERE id=?", (_at(hours=3), cid))
    db._conn.commit()
    db.finalize_charge(cid, types.SimpleNamespace(soc=84.0, latitude=1.0, longitude=2.0),
                       max_power_kw=3.2)
    assert _ac(db, cid) is None


def test_the_existing_column_survives_the_migration(tmp_path):
    """An old database gains the column and every charge already in it reads as never blind."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = _open_charge(db)
    assert _dark(db, cid) in (None, 0.0)


# ── the recorder counts the minutes it could not measure ─────────────────────

def test_the_recorder_reports_a_poll_home_assistant_did_not_answer(tmp_path, monkeypatch):
    """`sample_wallbox_meter` on a cycle where HA returns nothing must not pass silently."""
    from recorder import Recorder
    db = D.Database(str(tmp_path / "t.db"))
    db.ensure_vehicle("LVINDARK000000001", "C10", 2025)
    rec = Recorder(db, 1)
    cid = _open_charge(db)
    db.set_charge_wallbox_start(cid, 11.78)
    rec._active_charge_id = cid
    rec._charge_at_wallbox = True
    monkeypatch.setattr(Recorder, "_read_wallbox_energy", lambda self: None)
    rec.sample_wallbox_meter()
    assert (_dark(db, cid) or 0) == pytest.approx(rec.poll_interval / 60, abs=0.01)


def test_the_bundle_prints_the_unread_minutes(tmp_path, monkeypatch):
    """Triage has to see it: `stuck=` is in the charges section next to the other two marks, and
    a charge whose meter went unread must say so on the same line rather than look healthy."""
    import db_reader
    import diagnostics
    db = D.Database(str(tmp_path / "t.db"))
    db.ensure_vehicle("LVINDARK000000001", "C10", 2025)
    cid = _open_charge(db)
    db._conn.execute("UPDATE charges SET started_at=?, ended_at=?, wb_dark_min=86 WHERE id=?",
                     (_at(hours=12), _at(hours=1), cid))
    db._conn.commit()
    db._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "_CONN", None, raising=False)
    section = diagnostics._charges_section()
    assert "dark=86" in section, section[:400]
