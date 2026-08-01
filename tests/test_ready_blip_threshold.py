"""A 72-second power-off is a power-off — beta #19, @michapr.

He switched the car off between two drives for 72 seconds, and Mate reported the two trips as
sharing one power-on session: "the car was never switched off". It had seen the switch-off and
thrown it away, because ready=0 gaps shorter than the debounce were treated as signal blips.

The debounce was set to 90 s against "signal blips seen in the log". Measured afterwards across
eight bundles from three owners — 123 distinct READY switch-offs in three weeks — only THREE fall
under 90 s (38.8 s, 72.0 s, 89.8 s), and one of those three is his real one. The blips the number
was protecting against amount to a single 38.8 s event. 60 s still absorbs that one and stops
eating the rest.

The constant also did a second, unrelated job — the slack for matching a session to the trip it
belongs to, calibrated on the ~1 minute the gear-P trip-end lags behind ready-off. That one is NOT
the blip filter and keeps its own value: dropping it to 60 s would have left it exactly equal to
the lag it exists to absorb.
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader


def _pos(pdb, t, ready):
    pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,?)",
                      (t.isoformat(), ready))


def _two_drives_split_by(tmp_path, monkeypatch, off_seconds):
    """Two drives with a real power-off of `off_seconds` between them, sampled every 10 s."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    aS = now - timedelta(hours=2)
    aE = aS + timedelta(minutes=10)
    bS = aE + timedelta(seconds=off_seconds)
    bE = bS + timedelta(minutes=10)
    for tid, s, e in ((1, aS, aE), (2, bS, bE)):
        pdb._conn.execute(
            "INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,"
            "efficiency_kwh_100km,start_soc,end_soc) VALUES (?,1,?,?,4.0,26.0,95,93)",
            (tid, s.isoformat(), e.isoformat()))
    t = aS - timedelta(minutes=2)
    while t < aS:                                   # off before the first drive
        _pos(pdb, t, 0); t += timedelta(seconds=10)
    for s, e in ((aS, aE), (bS, bE)):               # on through each drive
        t = s
        while t <= e:
            _pos(pdb, t, 1); t += timedelta(seconds=10)
    t = aE + timedelta(seconds=10)                  # the switch-off between them
    while t < bS:
        _pos(pdb, t, 0); t += timedelta(seconds=10)
    t = bE + timedelta(seconds=10)                  # off after the second drive
    while t <= bE + timedelta(minutes=2):
        _pos(pdb, t, 0); t += timedelta(seconds=10)
    pdb._conn.commit()
    return pdb


def _n_trips(pdb, trip_id):
    trip = dict(pdb._conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone())
    s = db_reader.ready_session(trip)
    return s["n_trips"] if s else None


# ── the regression: 72 seconds is his case ────────────────────────────────────
def test_a_72_second_power_off_separates_the_two_drives(tmp_path, monkeypatch):
    pdb = _two_drives_split_by(tmp_path, monkeypatch, 72)
    assert _n_trips(pdb, 1) == 1, "the 72s switch-off was swallowed — the drives read as one session"
    assert _n_trips(pdb, 2) == 1


# ── and the blip it must still absorb ─────────────────────────────────────────
def test_a_39_second_dip_is_still_treated_as_a_blip(tmp_path, monkeypatch):
    """38.8 s is the only sub-minute dip in three weeks of real data across three cars — the one
    candidate for an actual signal blip. It must still merge, or an ordinary drive splits in two."""
    pdb = _two_drives_split_by(tmp_path, monkeypatch, 39)
    assert _n_trips(pdb, 1) == 2, "a 39s dip split a session that never ended"


def test_a_long_stop_obviously_separates_them(tmp_path, monkeypatch):
    pdb = _two_drives_split_by(tmp_path, monkeypatch, 600)
    assert _n_trips(pdb, 1) == 1
