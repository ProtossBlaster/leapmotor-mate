"""The getEC window opens when the car last said it was off, not when Mate wrote that down.

The cloud books a power-on's energy at one instant, the power-on itself, and a getEC window returns
it only when it starts at or before that instant. Mate starts it at the last reading that saw the
car off. A car falling asleep stops sending frames and the cloud serves the last one again, so the
rows Mate writes keep saying "off" with an old frame after the car may already be on. Started at
the time such a row was written, the window opened after the power-on and every read of the drive
came back empty for six hours. The moment of the frame (`frame_ts`, the car's clock) is the one the
"off" belongs to; it is used whenever it is earlier than the row. The window never reaches into the
drive before: it would take in that drive's power-on, and getEC would count that drive's energy as
this one's.
"""
from datetime import datetime, timedelta, timezone

import command_client
import db_reader
import ec_enrich
from test_ec_enrich_lock import _ec, _two_trips_only

POLL = timedelta(seconds=12)


def _write(pdb, rows):
    """(recorded_at, frame time or None, ready, gear) as positions rows."""
    pdb._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, frame_ts, ready, gear) VALUES (1,?,?,?,?)",
        [(t.isoformat(), int(f.timestamp() * 1000) if f else None, ready, gear)
         for t, f, ready, gear in rows])
    pdb._conn.commit()


def _trip(pdb, trip_id):
    return dict(pdb._conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone())


def _drive(t, polls):
    """`polls` fresh readings with the car on, from `t`."""
    return [(t + i * POLL, t + i * POLL, 1, "D") for i in range(polls)]


def test_a_drive_after_a_repeated_off_frame_gets_its_energy(tmp_path, monkeypatch):
    t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    off = t + POLL
    power_on = off + timedelta(seconds=18)
    rows = [(t, t, 0, "P"), (off, off, 0, "P")]
    rows += [(off + i * POLL, off, 0, "P") for i in (1, 2, 3)]          # the same frame, served again
    rows += _drive(off + 4 * POLL, 100)
    end = rows[-1][0]
    pdb = _two_trips_only(tmp_path, monkeypatch, off + 4 * POLL, end,
                          end + timedelta(hours=2), end + timedelta(hours=3))
    _write(pdb, rows + [(end + POLL, end + POLL, 0, "P")])
    asked = []

    def cloud(begin, finish):
        asked.append(begin)
        return _ec(9.1) if begin <= power_on.timestamp() <= finish else None
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", cloud)
    ec_enrich._sweep_now()

    assert asked == [off.timestamp()]
    assert pdb._conn.execute("SELECT ec_kwh FROM trips WHERE id=1").fetchone()[0] == 9.1


def test_a_car_clock_ahead_of_ours_keeps_the_row_time(tmp_path, monkeypatch):
    t = datetime(2026, 7, 28, 7, 56, tzinfo=timezone.utc)
    rows = [(t, t + timedelta(seconds=40), 0, "P")] + _drive(t + POLL, 30)
    pdb = _two_trips_only(tmp_path, monkeypatch, t + POLL, rows[-1][0],
                          rows[-1][0] + timedelta(hours=2), rows[-1][0] + timedelta(hours=3))
    _write(pdb, rows + [(rows[-1][0] + POLL, rows[-1][0] + POLL, 0, "P")])

    assert db_reader.trip_ec_window(_trip(pdb, 1))[0] == int(t.timestamp())


def test_the_window_never_reaches_into_a_drive_mate_rebuilt(tmp_path, monkeypatch):
    """A drive the car made out of touch sent no frames, so the "off" frame before the next one is
    older than it. Mate rebuilt it from what it saw on either side; a window reaching into it would
    count its energy as this drive's."""
    t = datetime(2026, 7, 28, 7, 56, tzinfo=timezone.utc)
    last_heard = t - timedelta(minutes=10)
    rebuilt_end = t + timedelta(minutes=30)
    off = rebuilt_end + timedelta(minutes=5)
    rows = [(last_heard, last_heard, 0, "P")]
    rows += [(off + i * POLL, last_heard, 0, "P") for i in range(3)]         # out of touch, then back
    rows += _drive(off + 3 * POLL, 30)
    pdb = _two_trips_only(tmp_path, monkeypatch, t, rebuilt_end, off + 3 * POLL, rows[-1][0])
    pdb._conn.execute("UPDATE trips SET reconstructed = 1 WHERE id = 1")
    _write(pdb, rows + [(rows[-1][0] + POLL, rows[-1][0] + POLL, 0, "P")])

    assert db_reader.trip_ec_window(_trip(pdb, 2))[0] >= int(rebuilt_end.timestamp())


def test_the_drive_before_ends_with_its_last_merged_piece(tmp_path, monkeypatch):
    """A merged drive's own row ends with its first piece; the window starts after its last one."""
    t = datetime(2026, 7, 28, 7, 56, tzinfo=timezone.utc)
    first = [(t - POLL, t - POLL, 0, "P")] + _drive(t, 30)
    stop = first[-1][0] + timedelta(minutes=3)
    second = [(stop, stop, 0, "P")] + _drive(stop + POLL, 30)
    second_end = second[-1][0]
    off = second_end + timedelta(minutes=5)
    rows = first + second + [(off, stop, 0, "P")] + _drive(off + POLL, 30)    # the stop's frame, served again
    pdb = _two_trips_only(tmp_path, monkeypatch, t, first[-1][0], stop + POLL, second_end)
    pdb._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km) VALUES (3,1,?,?,9.0)",
                      ((off + POLL).isoformat(), rows[-1][0].isoformat()))
    _write(pdb, rows + [(rows[-1][0] + POLL, rows[-1][0] + POLL, 0, "P")])
    assert db_reader.merge_trips(1, 2)["ok"] is True

    assert db_reader.trip_ec_window(_trip(pdb, 3))[0] >= int(second_end.timestamp())
