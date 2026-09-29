"""A row the web writes after a command or a Refresh keeps the clock of the frame it read.

The poller stores each frame's own timestamp with its row (`frame_ts`, the car's clock). The web's
write after a command or a Refresh did not, so its row read as a frame nobody had seen before, and
whatever tells a re-served frame from a new one took the web's copy for news. The missed-charge
scan collapses a re-served frame; a Refresh during an outage broke the silence there instead, and
the charge across it was never found.
"""
from datetime import datetime, timedelta, timezone

import client
import db_reader
import pytest
from test_missed_charge_scan import _seed

T0 = datetime(2026, 6, 1, 22, tzinfo=timezone.utc)


def _signal(minutes=0, soc=30.0, odometer=1000.0, **clock):
    """A frame on the move, stamped `minutes` after T0 unless `clock` says how it is stamped."""
    stamp = clock or {"1": int((T0 + timedelta(minutes=minutes)).timestamp() * 1000)}
    return {"100003": soc, "1318": odometer, "1319": 30.0, "1010": 3, "3": 45.0, "2": 9.0, **stamp}


def _frame_ts(db):
    return [r[0] for r in db._conn.execute("SELECT frame_ts FROM positions ORDER BY id")]


@pytest.mark.parametrize("clock, stored", [
    ({"sts": 1780351200000}, 1780351200000),
    ({"1": 1780351200000}, 1780351200000),
    ({"sts": "1780351200000", "1": 1}, 1780351200000),
    ({"1": 0}, None),
    ({"sts": ""}, None),
    ({"2": 9.0}, None),
], ids=["sts", "signal 1", "sts first", "zero", "empty", "none"])
def test_the_web_stores_the_frames_clock_as_the_poller_does(tmp_path, monkeypatch, clock, stored):
    """A frame without a clock is stored without one: the time of writing is no frame's clock."""
    db = _seed(tmp_path, monkeypatch)
    sig = _signal(**clock)
    db.save_position(1, client._parse_signal("VIN1", sig))
    db_reader.save_fresh_signals(sig)
    assert _frame_ts(db) == [stored, stored]


def test_a_refresh_during_an_outage_does_not_hide_the_charge_across_it(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    before = _signal(0, soc=30.0)
    db.save_position(1, client._parse_signal("VIN1", before))     # the last poll before the silence
    db_reader.save_fresh_signals(before)                           # Refresh: the cloud re-serves it
    db.save_position(1, client._parse_signal("VIN1", _signal(240, soc=80.0, odometer=1002.0)))
    for row_id, hours in ((1, 0), (2, 2), (3, 4)):                 # when each row was written
        db._conn.execute("UPDATE positions SET recorded_at=? WHERE id=?",
                         ((T0 + timedelta(hours=hours)).isoformat(), row_id))
    db._conn.commit()
    candidates = db_reader.scan_missed_charges()
    assert [(c["start_soc"], c["end_soc"]) for c in candidates] == [(30, 80)]


def test_a_refresh_does_not_hide_how_old_the_data_is(tmp_path, monkeypatch):
    """The Overview reads the latest row, which after a Refresh is the web's."""
    db = _seed(tmp_path, monkeypatch)
    frozen = _signal(**{"1": int((datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp() * 1000)})
    db.save_position(1, client._parse_signal("VIN1", frozen))
    db_reader.save_fresh_signals(frozen)
    assert db_reader.get_latest_status()["data_age"] == "30m"
