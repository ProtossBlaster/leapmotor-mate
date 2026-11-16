"""A frame the cloud repeats is no new reading of READY (#487).

Parked in an underground garage, a B10 stopped sending: for 2 h 40 min every poll got back the
frame of the last moment it had signal, in gear D with READY=1. Mate stored each poll as a reading,
so `ready_session` saw READY=1 from the end of the morning drive to the start of the next one and
called them one power-on. The trip page then refused the official figure ("shares one power-on
session, merge them"), and the merge refused the 160-minute gap. The car had been off.

A poll that brings back a frame already read says nothing about the car since that frame. It
counts as a poll that didn't report READY: out of P, it joins two READY=1 runs only within the
carry window, as it does when READY is missing.
"""
from datetime import datetime, timedelta, timezone

import command_client
import db_reader
import ec_enrich
from test_ec_enrich_lock import _ec, _two_trips_only

T0 = datetime(2026, 11, 10, 7, 35, tzinfo=timezone.utc)
POLL = timedelta(seconds=10)


def _write(pdb, rows):
    """rows = [(time, ready, gear, frame_ts)]"""
    pdb._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, ready, gear, frame_ts) VALUES (1,?,?,?,?)",
        [(t.isoformat(), r, g, f) for t, r, g, f in rows])
    pdb._conn.commit()


def _fresh(t, n, ready, gear):
    """n polls, each a new frame stamped at its own poll time."""
    out = []
    for _ in range(n):
        out.append((t, ready, gear, int(t.timestamp() * 1000)))
        t += POLL
    return out, t


def _frozen_garage(gap):
    """The morning drive, then `gap` of polls repeating its last frame (D, READY=1), then the
    next drive, then a park seen off."""
    rows, t = _fresh(T0 - 3 * POLL, 3, 0, "P")
    drive, t = _fresh(t, 300, 1, "D")                       # 50 minutes
    rows += drive
    a_start, a_end = drive[0][0], drive[-1][0]
    last_frame = drive[-1][3]
    end_of_gap = t + gap
    while t < end_of_gap:
        rows.append((t, 1, "D", last_frame))
        t += POLL
    drive, t = _fresh(t, 270, 1, "D")                       # 45 minutes
    rows += drive
    b_start, b_end = drive[0][0], drive[-1][0]
    park, t = _fresh(t, 6, 0, "P")
    rows += park
    return rows, a_start, a_end, b_start, b_end


def _trip(pdb, trip_id):
    return dict(pdb._conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone())


def test_a_frozen_frame_between_two_drives_is_not_one_power_on(tmp_path, monkeypatch):
    rows, a_start, a_end, b_start, b_end = _frozen_garage(timedelta(minutes=160))
    pdb = _two_trips_only(tmp_path, monkeypatch, a_start, a_end, b_start, b_end)
    _write(pdb, rows)

    assert db_reader.ready_session(_trip(pdb, 1))["trip_ids"] == [1]
    assert db_reader.ready_session(_trip(pdb, 2))["trip_ids"] == [2]
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(9.0))
    assert ec_enrich.convert_trip(2).get("reason") != "shared_session"


def test_a_frame_repeated_for_a_poll_or_two_still_joins_the_drive(tmp_path, monkeypatch):
    """The cloud often hands back the same frame for a poll or two while the car is on: that is
    well inside the carry window and must not split a drive in two."""
    rows, a_start, a_end, b_start, b_end = _frozen_garage(timedelta(seconds=40))
    pdb = _two_trips_only(tmp_path, monkeypatch, a_start, a_end, b_start, b_end)
    _write(pdb, rows)

    assert db_reader.ready_session(_trip(pdb, 1))["trip_ids"] == [1, 2]


def test_a_new_frame_with_the_same_ready_is_still_a_reading(tmp_path, monkeypatch):
    """Without frame_ts (rows written before it existed) nothing is treated as a repeat."""
    rows, a_start, a_end, b_start, b_end = _frozen_garage(timedelta(minutes=10))
    rows = [(t, r, g, None) for t, r, g, _ in rows]
    pdb = _two_trips_only(tmp_path, monkeypatch, a_start, a_end, b_start, b_end)
    _write(pdb, rows)

    assert db_reader.ready_session(_trip(pdb, 1))["trip_ids"] == [1, 2]
