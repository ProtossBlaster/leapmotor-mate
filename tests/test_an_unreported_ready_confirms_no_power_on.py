"""A poll that didn't report READY says nothing about the power-on it falls in.

`ready_session` tells the rest of Mate whether a trip shares one power-on with others: the
automatic getEC read leaves a shared trip alone, a merge beyond the usual gap is allowed only
inside one, and the getEC window starts at the last reading that saw the car off. It used to fill a
poll without a READY value with the last value seen, for up to 15 minutes. That turned a stop in P
with no READY into proof that the car stayed on — two drives came back as one power-on, however
short the stop — and turned a zero seen before the gap into a zero seen at its end, a moment the
car may already have been on.

Unreported polls now say no more than a zero would. In P they join the READY=1 runs around them
only as briefly as a switch-off seen for a moment, out of P within the old window: a trip closes in
P, or in gear only when a frozen frame has held for half an hour. A session split at them is one
that could not be confirmed, not a switch-off; and the getEC window starts only at a zero that was
actually read.
"""
from datetime import datetime, timedelta, timezone

import pytest

import command_client
import db_reader
import ec_enrich
from test_ec_enrich_lock import _ec, _two_trips_only

T0 = datetime(2026, 7, 28, 7, 56, tzinfo=timezone.utc)
POLL = timedelta(seconds=10)


class _Log:
    """Polls every 10 s from T0, each with its READY value (1, 0 or None) and gear."""

    def __init__(self, start=T0):
        self.rows, self.t = [], start

    def polls(self, readies, gear):
        for ready in readies:
            self.rows.append((self.t, ready, gear))
            self.t += POLL
        return self.t - POLL

    def write(self, pdb):
        pdb._conn.executemany(
            "INSERT INTO positions (vehicle_id, recorded_at, ready, gear) VALUES (1,?,?,?)",
            [(t.isoformat(), ready, gear) for t, ready, gear in self.rows])
        pdb._conn.commit()


def _drive(log, polls, every):
    """A drive reporting READY=1 on every `every`-th poll, on its first and on its last."""
    return log.polls([1 if i % every == 0 or i == polls - 1 else None for i in range(polls)], "D")


def _trip(pdb, trip_id):
    return dict(pdb._conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone())


def _epoch(t):
    return int(t.timestamp())


@pytest.mark.parametrize("every", [1, 10], ids=["READY every poll", "READY every tenth poll"])
@pytest.mark.parametrize("stop", [
    [1] * 5 + [None] * 31,                  # six minutes in P, READY unreported after the close
], ids=["six minutes in P"])
def test_unreported_polls_between_two_drives_confirm_no_shared_session(tmp_path, monkeypatch,
                                                                         every, stop):
    log = _Log()
    log.polls([0, 0, 0], "P")                            # seen off before the first drive
    a_start = _drive(log, 30, every) - 29 * POLL
    a_end = log.polls(stop, "P") - (len(stop) - 6) * POLL
    b_start = _drive(log, 30, every) - 29 * POLL
    b_end = log.polls([0] * 6, "P")
    pdb = _two_trips_only(tmp_path, monkeypatch, a_start, a_end, b_start, b_end)
    log.write(pdb)

    assert db_reader.ready_session(_trip(pdb, 1))["trip_ids"] == [1]
    assert db_reader.ready_session(_trip(pdb, 2))["trip_ids"] == [2]
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(2.3))
    assert ec_enrich.convert_trip(2).get("reason") != "shared_session"
    assert db_reader.merge_trips(1, 2, gap_min=0)["error"] == "gap_too_large", \
        "a merge beyond the usual gap needs a confirmed shared power-on"
    begin, _ = db_reader.trip_ec_window(_trip(pdb, 2))
    assert begin >= _epoch(a_end), "the second drive's getEC window reaches back into the first"


def _two_drives(log, gap_s, between, gear):
    """Two drives reporting READY=1 on every poll, `gap_s` apart from the last READY=1 of the first
    to the first of the second, with one poll of READY `between` in `gear` halfway."""
    log.polls([0, 0, 0], "P")
    a_start = log.polls([1] * 30, "D") - 29 * POLL
    last_on = a_start + 29 * POLL
    log.t = last_on + timedelta(seconds=gap_s / 2)
    a_end = log.polls([between], gear)
    log.t = last_on + timedelta(seconds=gap_s)
    b_start = log.polls([1] * 30, "D") - 29 * POLL
    return a_start, a_end, b_start, log.polls([0] * 6, "P")


def _recent():
    """A log the automatic getEC read still reaches: it re-reads trips for a few hours after them."""
    return _Log(datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2))


def _read_as_one_power_on(pdb, monkeypatch):
    """What Mate's consumers make of the two drives: the automatic getEC read (left alone when
    shared), a manual conversion and a merge beyond the usual gap. True/False when all agree."""
    read = []
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: read.append((b, e)) or _ec(2.3))
    ec_enrich._sweep_now()
    swept_alone = not read
    converted = ec_enrich.convert_trip(2).get("reason") == "shared_session"
    merged = db_reader.merge_trips(1, 2, gap_min=0).get("error") != "gap_too_large"
    assert swept_alone == converted == merged, (swept_alone, converted, merged)
    return merged


@pytest.mark.parametrize("gap_s, one", [(59, True), (60, False), (61, False)])
@pytest.mark.parametrize("between", [0, None], ids=["READY 0", "no READY"])
def test_a_stop_in_p_without_ready_joins_two_drives_only_as_a_zero_would(tmp_path, monkeypatch,
                                                                         gap_s, one, between):
    """A poll in P that didn't report READY says no more than one that read the car off: both join
    the drives around them into one power-on only for a stop shorter than a blip."""
    log = _recent()
    pdb = _two_trips_only(tmp_path, monkeypatch, *_two_drives(log, gap_s, between, "P"))
    log.write(pdb)
    assert _read_as_one_power_on(pdb, monkeypatch) is one


@pytest.mark.parametrize("gap_s, one", [(900, True), (910, False)])
def test_unreported_polls_out_of_p_join_two_drives_only_within_the_window(tmp_path, monkeypatch,
                                                                          gap_s, one):
    log = _recent()
    pdb = _two_trips_only(tmp_path, monkeypatch, *_two_drives(log, gap_s, None, "D"))
    log.write(pdb)
    assert _read_as_one_power_on(pdb, monkeypatch) is one


def test_a_frozen_frame_without_ready_joins_nothing_past_the_window(tmp_path, monkeypatch):
    """The frozen-frame guard closes a trip in gear: one frame without READY, repeated for half an
    hour (only its first poll and those after the close are saved), then the next drive. Out of P
    is no proof the car stayed on."""
    log = _Log()
    log.polls([0, 0, 0], "P")
    a_start = log.polls([1] * 30, "D") - 29 * POLL
    a_end = log.polls([None], "D")
    log.t += timedelta(minutes=30)
    log.polls([None] * 6, "D")
    b_start = log.polls([1] * 30, "D") - 29 * POLL
    b_end = log.polls([0] * 6, "P")
    pdb = _two_trips_only(tmp_path, monkeypatch, a_start, a_end, b_start, b_end)
    log.write(pdb)

    assert db_reader.ready_session(_trip(pdb, 1))["trip_ids"] == [1]
    assert db_reader.ready_session(_trip(pdb, 2))["trip_ids"] == [2]
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(2.3))
    assert ec_enrich.convert_trip(2).get("reason") != "shared_session"
    assert db_reader.merge_trips(1, 2, gap_min=0)["error"] == "gap_too_large"


def test_unreported_polls_inside_a_drive_keep_it_one_session(tmp_path, monkeypatch):
    log = _Log()
    off = log.polls([0, 0, 0], "P")
    a_start = log.polls([1] * 20, "D") - 19 * POLL
    log.polls([None] * 30, "D")                          # five minutes on the road without READY
    log.polls([1] * 20, "D")
    a_end = log.polls([0] * 6, "P")
    pdb = _two_trips_only(tmp_path, monkeypatch, a_start, a_end,
                          a_end + timedelta(hours=2), a_end + timedelta(hours=3))
    log.write(pdb)

    session = db_reader.ready_session(_trip(pdb, 1))
    assert session["trip_ids"] == [1] and session["on_lo"] == _epoch(off)
    assert db_reader.trip_ec_window(_trip(pdb, 1))[0] == _epoch(off)


def test_the_window_starts_at_a_zero_that_was_read_not_one_carried(tmp_path, monkeypatch):
    log = _Log()
    off = log.polls([0], "P")
    log.polls([None] * 20, "P")                          # parked, READY unreported
    a_start = log.polls([1] * 30, "D") - 29 * POLL
    a_end = log.polls([0] * 6, "P")
    pdb = _two_trips_only(tmp_path, monkeypatch, a_start, a_end,
                          a_end + timedelta(hours=2), a_end + timedelta(hours=3))
    log.write(pdb)

    assert db_reader.ready_session(_trip(pdb, 1))["on_lo"] == _epoch(off)
    assert db_reader.trip_ec_window(_trip(pdb, 1))[0] == _epoch(off)


def test_unreported_polls_after_the_last_on_do_not_stretch_the_session(tmp_path, monkeypatch):
    """The first drive stops reporting READY before it ends, the second never reports it: nothing
    says the car stayed on from one to the other."""
    log = _Log()
    log.polls([0, 0, 0], "P")
    a_start = log.polls([1] * 30, "D") - 29 * POLL
    last_on = a_start + 29 * POLL
    log.polls([None] * 20, "D")
    a_end = log.polls([None] * 6, "P")
    log.polls([None] * 18, "P")
    b_start = log.polls([None] * 30, "D") - 29 * POLL
    b_end = log.polls([0] * 6, "P")
    pdb = _two_trips_only(tmp_path, monkeypatch, a_start, a_end, b_start, b_end)
    log.write(pdb)

    session = db_reader.ready_session(_trip(pdb, 1))
    assert session["off"] == _epoch(last_on) and 2 not in session["trip_ids"]
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(2.3))
    assert ec_enrich.convert_trip(1).get("reason") != "shared_session"
