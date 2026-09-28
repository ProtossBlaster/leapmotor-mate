"""The web's write after a command keeps the READY it read.

After a command or a refresh the web saves the car's fresh reading into `positions`, the table the
poller fills. It left READY out although signal 1258 was in the reading it had in hand, so every
such row read as a poll that did not report READY — in Park with the car on, a hole in a power-on
the car never had.
"""
import sqlite3

import db as D
import db_reader
import pytest
from test_a_missing_speed_or_odometer_is_stored_as_unknown import _ABSENT, _signal


@pytest.mark.parametrize("sent, stored", [(1, 1), ("1", 1), (0, 0), (_ABSENT, None), (None, None)])
def test_the_row_saved_after_a_command_keeps_the_ready_the_car_sent(tmp_path, monkeypatch,
                                                                     sent, stored):
    path = str(tmp_path / "web.db")
    D.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'TESTVIN')")
    con.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    sig = _signal(speed=0, odometer=12345)
    if sent is not _ABSENT:
        sig["1258"] = sent
    db_reader.save_fresh_signals(sig)
    stored_ready = con.execute("SELECT ready FROM positions").fetchone()[0]
    con.close()
    assert stored_ready == stored
