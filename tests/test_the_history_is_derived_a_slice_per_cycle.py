"""An install with history reads it back a slice per round of the poll loop, between the polls.

There is no separate backfill: the detector starts at the beginning of `positions` and each round
of the loop moves it on by one batch of 5000 rows, after the cars were polled. The polls do not wait
for the history, and a car whose derivation raised does not stop the round. The state says how far
the history has been read, in rows, until it has been read through. The loop is the real one,
`main()`, against a stub cloud.
"""
from types import SimpleNamespace

import db as D
import events as E
from events_fixture import T0
from test_poller_loop_drives_each_vehicle import PM, _Client, _frame, _Stop, _Vehicle

HISTORY = 12_000


def _run_rounds(tmp_path, monkeypatch, rounds):
    path = str(tmp_path / "loop.db")
    monkeypatch.setenv("DB_PATH", path)
    client = _Client([_frame(ts=1_760_000_000_000 + i * 30_000) for i in range(rounds)])
    client.budget = rounds
    monkeypatch.setattr(PM, "LeapmotorMateClient", lambda **kw: client)
    clock = {"t": 1_760_000_000.0}
    # The poller's own `time`, not the process's: the web app's threads sleep on that one too.
    monkeypatch.setattr(PM, "time", SimpleNamespace(time=lambda: clock["t"],
                                                    sleep=lambda s: clock.__setitem__("t", clock["t"] + s)))
    monkeypatch.setattr(PM, "_maybe_refresh_charge_schedule", lambda *a, **k: None)
    monkeypatch.setattr(PM.energy_snapshots, "maybe_sample", lambda *a, **k: None)
    monkeypatch.setattr(PM.ready_automation, "maybe_trigger", lambda *a, **k: None)
    monkeypatch.setattr(PM, "_mqtt_tick", lambda db, c, d, s: None)
    monkeypatch.setattr(PM, "load_config", lambda db: {
        "username": "u", "password": "p", "pin": "1234",
        "cert_path": "/tmp/c.pem", "key_path": "/tmp/k.pem"})

    database = D.Database(path)
    database.set_setting("setup_complete", "1")
    vid = database.ensure_vehicle(_Vehicle().vin, "B10")
    base = int(T0.timestamp() * 1000)
    database._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, frame_ts, is_locked, gear, speed_kmh)"
        " VALUES (?, '2026-09-01T00:00:00+00:00', ?, ?, 'P', 0)",
        ((vid, base + i * 30_000, 0 if 100 <= i < 200 else 1) for i in range(HISTORY)))
    database._conn.commit()
    database._conn.close()
    try:
        PM.main()
    except _Stop:
        pass
    db = D.Database(path)
    return client, db, E.load_state(db._conn, vid)


def test_each_round_reads_one_batch_after_the_poll(tmp_path, monkeypatch, caplog):
    with caplog.at_level("INFO", logger="events"):
        client, _db, state = _run_rounds(tmp_path, monkeypatch, rounds=1)
    assert client.calls == 1, "the car was polled before any history was read"
    assert state["cursor_id"] == 5000
    assert not state["caught_up"] and state["pct"] == 41                  # 5000 of 12 001 rows
    assert "5000 rows read" in caplog.text and "41 % of history" in caplog.text


def test_three_rounds_read_the_whole_history_and_the_new_rows(tmp_path, monkeypatch):
    client, db, state = _run_rounds(tmp_path, monkeypatch, rounds=3)
    assert client.calls == 3
    assert state["cursor_id"] == HISTORY + 3, "the history and the three polled frames"
    assert state["caught_up"] and state["pct"] == 100
    kinds = [(r[0], r[1]) for r in db._conn.execute("SELECT kind, state FROM events ORDER BY id")]
    assert kinds == [("unlocked", 1), ("unlocked", 0)]


def test_a_car_whose_derivation_fails_does_not_stop_the_round(tmp_path, monkeypatch, caplog):
    def boom(self, vehicle_id, max_rows=5000):
        raise RuntimeError("no events today")
    monkeypatch.setattr(D.Database, "derive_events", boom)
    with caplog.at_level("WARNING", logger="leapmotor_mate"):
        client, db, _ = _run_rounds(tmp_path, monkeypatch, rounds=2)
    assert client.calls == 2
    assert "Events not derived" in caplog.text
    assert float(db.get_setting("last_loop_ts", "0")) > 0
