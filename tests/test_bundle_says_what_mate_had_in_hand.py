"""For every charge the CAR took while parked, what Mate had in hand at the time.

#230, @adoewa, 06/08/26. His C10 went 49.8% → 90.0% over 3¼ hours and Mate opened nothing. Proving
what happened took half an hour over 30 000 log lines, and the one question that decides which fix
is right — *what did the cable signal say during those three hours* — could not be answered at all:
the bundle carries the raw signals as a SINGLE snapshot, taken when the user presses the button,
which for him was ten hours after the cable came out.

The values were never missing. `positions` holds one row per poll with `plug_connected`, `charging`,
`charge_current_a` and `frame_ts`; his 202 rows were on his disk the whole time. Nothing read them.

Silvio, 06/08: *«si analizza, si verifica il problema e solo alla fine si risolve perché si è
trovato il problema, non si va a tentativi»*. This section is the analysis step made automatic.

🔑 **What it must separate, because it is the whole question:**

    frames 202/202 distinct   → the car was ONLINE and the cloud stayed silent
    frames   1/477 distinct   → the car was OFFLINE and the cloud repeated its last frame

Both shapes appear in his own night, four hours apart. The second is the ordinary "went into a dead
zone and came back charged" that the reconstructor already covers; the first is the one nothing
covers, and telling them apart by eye took counting distinct values across thousands of log lines.

⚠️ The rises that WERE recorded are listed too. A defect with no control group beside it is a
coincidence — the two good charges are what proved Mate's own machinery works and moved the
question onto the cloud.
"""
import pathlib
import re

import db as PollerDB
import db_reader
import diagnostics
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = PollerDB.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)

    # `frame="auto"` = one fresh frame per poll; `frame=None` writes a real NULL, which is what a
    # database older than the column looks like. The first version of this helper turned None into
    # the timestamp, so the test for that case could never fail.
    def poll(ts, soc, *, charging=0, plug=0, amps=0.0, frame="auto", speed=0.0, gear="P"):
        pdb._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, plug_connected,"
            " charge_current_a, frame_ts, speed_kmh, gear) VALUES (1,?,?,?,?,?,?,?,?)",
            (ts, soc, charging, plug, amps, ts if frame == "auto" else frame, speed, gear))
        pdb._conn.commit()

    def charge(started, ended, s0, s1):
        pdb._conn.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
            " energy_added_kwh) VALUES (1,?,?,?,?,10.0)", (started, ended, s0, s1))
        pdb._conn.commit()

    return poll, charge


def _section():
    body = diagnostics.build_bundle("9.9.9")
    if "----- charges the car took" not in body:
        return ""
    return body.split("----- charges the car took", 1)[1].split("\n-----", 1)[0]


def _rise(poll, day, n=40, step=0.5, soc0=50.0, **kw):
    """A slow climb, one small step at a time — the shape that defeats the reconstructor."""
    for i in range(n):
        poll(f"2026-08-{day:02d}T{4 + i // 30:02d}:{i % 30 * 2:02d}:00+00:00", soc0 + i * step, **kw)


# ── the missed one, named ─────────────────────────────────────────────────────

def test_a_rise_with_no_charge_row_is_flagged(car):
    poll, _ = car
    _rise(poll, 6)
    out = _section()
    assert "50.0" in out and "69.5" in out
    assert re.search(r"(?i)no charge|not recorded|nessuna", out), "the missed rise is not flagged"


def test_a_rise_that_was_recorded_is_shown_as_such(car):
    """The control group. Without it a single flagged line is a coincidence, not a finding."""
    poll, charge = car
    _rise(poll, 6, charging=1, plug=1, amps=-11.9)
    charge("2026-08-06T04:00:00+00:00", "2026-08-06T05:20:00+00:00", 50.0, 69.5)
    out = _section()
    assert not re.search(r"(?i)no charge|not recorded", out), out


# ── the distinction the whole investigation turned on ─────────────────────────

def test_a_live_window_is_told_apart_from_a_repeated_frame(car):
    """His own night held both, four hours apart: 202 polls carrying 202 different frames, and 477
    polls carrying ONE frame aged up to nine hours."""
    poll, _ = car
    _rise(poll, 6)                                    # every poll its own frame_ts
    out = _section()
    assert "40/40" in out or "40 of 40" in out, out


def test_a_repeated_frame_is_visible_as_such(car):
    poll, _ = car
    for i in range(40):
        poll(f"2026-08-06T04:{i:02d}:00+00:00", 50.0 + i * 0.5,
             frame="2026-08-05T19:00:00+00:00")       # the cloud re-serving one old frame
    out = _section()
    assert "1/40" in out or "1 of 40" in out, out


# ── the three signals that decide, and their absence ──────────────────────────

def test_it_reports_the_cable_the_decision_and_the_current(car):
    """The three inputs to `_is_charging`. #230's line would read 0 / 0 / 0.0 for 202 polls."""
    poll, _ = car
    _rise(poll, 6, plug=1, charging=0, amps=-0.4)
    out = _section()
    assert "plug=40" in out
    assert "chg=0" in out
    assert "-0.4" in out


def test_driving_is_not_a_charge(car):
    """Regen while driving pushes the SoC up and the current negative — the exact pair that would
    read as a charge. Same motion gate the poller uses."""
    poll, _ = car
    _rise(poll, 6, speed=70.0, gear="D", amps=-30.0)
    assert not re.search(r"(?i)no charge|not recorded", _section())


def test_a_wobble_under_the_threshold_is_not_listed(car):
    """A parked battery drifts. Below the reconstructor's own floor there is nothing to report,
    and a page of 0.4% 'missed charges' would bury the one that matters."""
    poll, _ = car
    _rise(poll, 6, n=4, step=0.1)
    assert not re.search(r"(?i)no charge|not recorded", _section())


def test_the_threshold_is_the_one_the_reconstructor_uses():
    """🔑 One idea of 'a real rise', not two. Two copies of a rule that drift apart is the defect
    family this release exists to close."""
    rec = (ROOT / "poller" / "recorder.py").read_text()
    dia = (ROOT / "web" / "diagnostics.py").read_text()
    floor = re.search(r"_reconstruct_min_pct: float = ([\d.]+)", rec).group(1)
    body = dia.split("def _missed_charges_section(", 1)[1].split("\ndef ", 1)[0]
    used = re.search(r"_RISE_MIN_PCT", dia)
    assert used, "the section does not name its threshold"
    assert re.search(rf"_RISE_MIN_PCT\s*=\s*{re.escape(floor)}", dia), \
        f"the bundle's floor drifted from the reconstructor's {floor}"
    assert "_RISE_MIN_PCT" in body


def test_a_database_without_frame_ts_does_not_claim_a_repeated_frame(car):
    """🔴 `frame_ts` is a recent column. On a database that predates it every row is NULL, and
    counting distinct values reads **1/N** — "the cloud repeated one frame for fifteen hours" —
    about charges that went perfectly. Found on Silvio's own database: 25 761 rows carry it out of
    207 287, so every episode older than the column read as a dead zone. Absent is not repeated."""
    poll, _ = car
    for i in range(40):
        poll(f"2026-08-06T04:{i:02d}:00+00:00", 50.0 + i * 0.5, frame=None)
    out = _section()
    assert "1/40 distinct" not in out, "a missing column was reported as a repeated frame"
    assert "n/a" in out


def test_the_window_really_is_fifteen_days(car):
    """The cutoff reads `recorded_at`; `positions` has no `started_at`. Getting that wrong threw a
    sqlite3.Error, which the helper answers with 'no cutoff' — so a real database was scanned whole,
    207 000 rows, and episodes from two months back were listed as if recent. The fixture never
    caught it because ten rows fit in any window."""
    poll, _ = car
    for i in range(6):                                   # a rise 40 days ago
        poll(f"2026-06-01T04:{i:02d}:00+00:00", 40.0 + i)
    for i in range(6):                                   # and one now
        poll(f"2026-08-06T04:{i:02d}:00+00:00", 50.0 + i)
    out = _section()
    assert "2026-08-06" in out
    assert "2026-06-01" not in out, "the 15-day window is not being applied"


def test_it_does_not_leak_positions(car):
    """`positions` carries coordinates. This section reads it and must publish none of them."""
    poll, _ = car
    _rise(poll, 6)
    db = PollerDB.Database(db_reader.DB_PATH)
    db._conn.execute("UPDATE positions SET latitude = 45.4642, longitude = 9.1900")
    db._conn.commit()
    body = diagnostics.build_bundle("9.9.9")
    assert "45.4642" not in body and "9.1900" not in body


def test_the_bundle_names_the_charge_detection_floor(car):
    """🔴 #230's real cause, and the reason it took half a day: `charge_detect_min_a` was **14.5 A**
    where the default is 2.0. A home AC charge moves the pack at 11-12 A, so `_is_charging` returned
    False on every one of 202 polls while the battery went 49.8% → 90.0%.

    The bundle reported the vampire-drain thresholds and NOT this one — the single setting that
    decides whether a charge is seen at all. With it on the first page the answer was thirty seconds
    away. @adoewa found it himself, in his own Settings, while we were reading his logs."""
    db_reader.set_setting("charge_detect_min_a", "14.5")
    body = diagnostics.build_bundle("9.9.9")
    assert "14.5" in body, "the charge-detection floor is not in the bundle"
    assert "Charge detect" in body
