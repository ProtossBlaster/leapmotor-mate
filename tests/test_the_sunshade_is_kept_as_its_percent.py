"""The sunshade is kept as how far it is open (signal 1724, the percent the car's screen shows).

`positions.sunshade_open` held 0/1 from the poller and the raw percent from the web's write after a
command, so the column meant two things. Both writers now store the percent in `sunshade_pct`;
an older database keeps the old column, unused, since a 0/1 cannot be turned back into a percent.
Everything shown from it (MQTT ON/OFF, the Commands tile, the check that a command took) reads as
before.
"""
import re
import sqlite3

import client
import db as D
import db_reader
import pytest
import recorder as R
import schema as S
from events_fixture import Car, web
from test_a_missing_speed_or_odometer_is_stored_as_unknown import _signal


@pytest.mark.parametrize("sent, stored", [(40, 40), ("40", 40), (0, 0), (None, None)])
def test_the_poller_stores_the_percent_the_car_sent(tmp_path, sent, stored):
    sig = _signal(speed=0, odometer=12345)
    if sent is not None:
        sig["1724"] = sent
    db = D.Database(str(tmp_path / "t.db"))
    rec = R.Recorder(db, vehicle_id=db.ensure_vehicle("TESTVIN", "B10"))
    rec.process(client._parse_signal("TESTVIN", sig))
    assert db._conn.execute("SELECT sunshade_pct FROM positions").fetchone()[0] == stored


@pytest.mark.parametrize("sent, stored", [(40, 40), ("40", 40), (0, 0), (None, None)])
def test_the_web_stores_the_same_after_a_command(tmp_path, monkeypatch, sent, stored):
    path = str(tmp_path / "web.db")
    D.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'TESTVIN')")
    con.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    sig = _signal(speed=0, odometer=12345)
    if sent is not None:
        sig["1724"] = sent
    db_reader.save_fresh_signals(sig)
    assert con.execute("SELECT sunshade_pct FROM positions").fetchone()[0] == stored
    con.close()


@pytest.mark.parametrize("pct, payload", [(40, "ON"), (100, "ON"), (0, "OFF"), (None, "OFF")])
def test_mqtt_still_says_open_or_closed(pct, payload):
    pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho")
    from dataclasses import replace

    from test_mqtt_ready import _data, _service
    svc = _service()
    svc._publish_sensors(replace(_data(False), sunshade_pct=pct))
    assert svc.client.published["leapmotor/VINTEST/sunshade_open"] == payload


def _old_positions(path):
    """`positions` as an install from before the percent has it: `sunshade_open`, no `sunshade_pct`."""
    conn = sqlite3.connect(path)
    conn.executescript(S.SCHEMA)
    conn.execute("ALTER TABLE positions DROP COLUMN sunshade_pct")
    conn.execute("ALTER TABLE positions ADD COLUMN sunshade_open INTEGER DEFAULT NULL")
    conn.execute("INSERT INTO positions (vehicle_id, recorded_at, sunshade_open) VALUES (1, 'x', 1)")
    conn.commit()
    conn.close()


def test_the_migration_adds_the_percent_and_leaves_the_flag_alone(tmp_path):
    """No `DROP COLUMN`: it would rewrite the whole of `positions` under a write lock at the first
    start of every install."""
    path = str(tmp_path / "mate.db")
    _old_positions(path)
    conn = sqlite3.connect(path)
    try:
        S.ensure_schema(conn)
        row = conn.execute("SELECT sunshade_pct, sunshade_open FROM positions").fetchone()
        assert row == (None, 1)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)")]
        S.ensure_schema(conn)                       # a second run is a no-op
        assert [r[1] for r in conn.execute("PRAGMA table_info(positions)")] == cols
    finally:
        conn.close()


def test_a_car_that_does_not_send_1724_still_reads_closed_on_commands(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    car.frame(**{"1724": None})
    tile = client.get("/api/cmd-grid").text
    tile = tile[tile.index("<!-- Sunshade -->"):]
    assert re.search(r'rounded-full[^>]*>\s*([^<]*?)\s*<', tile).group(1) == "○ Closed"


@pytest.mark.parametrize("cmd, before, after", [("open_sunshade", 0, 100), ("close_sunshade", 100, 0)])
def test_a_sunshade_command_waits_for_the_car_to_move(monkeypatch, cmd, before, after):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    import main
    from test_command_verify import _patch
    readings = iter([before, before, after])
    _, calls = _patch(monkeypatch, lambda: {"1724": next(readings, after)})
    main._command_epoch = 4
    main._post_command_refresh(main._OPTIMISTIC[cmd], epoch=4, delay=3, deadline_s=30)
    assert [s["1724"] for s in calls["save"]] == [after] and calls["clear"] == 0


@pytest.mark.parametrize("cmd, before", [("open_sunshade", 0), ("close_sunshade", 100)])
def test_a_sunshade_that_does_not_move_runs_out_the_wait(monkeypatch, cmd, before):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    import main
    from test_command_verify import _patch
    _, calls = _patch(monkeypatch, lambda: {"1724": before})
    main._command_epoch = 4
    main._post_command_refresh(main._OPTIMISTIC[cmd], epoch=4, delay=3, deadline_s=30)
    assert calls["clear"] == 1
