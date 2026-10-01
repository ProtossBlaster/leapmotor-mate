"""A range of 0 km beside a charged battery is not a reading (#365).

@arzthilfe, C10, 01/10/2026: the Overview read 100% and 0 km. His bundle shows how. The charge
ended at 02:54 at 100% and 426 km; he drove 2 km at 07:08 and parked at 07:16, still 426 km. At
07:18:51 — the first frame after the car stopped publishing, its age climbing from 07:18:06 — the
cloud sent `3260` (battery range) = 0 with `1204` (SoC) still 100, and every frame after it said the
same until the bundle was taken. Ten days of his history: eight such stops, 1,996 polls, SoC 83-100%
each time, while the smallest range the car reported otherwise was 180 km. Six B10 bundles, a T03
and a B03X never send it; neither does Silvio's B10 in 382,288 stored rows.

`float(sig.get("3260") or 0)` turned that zero, and an absent range, into a measured 0 km. The rule
now mirrors the one that already guards the SoC (a SoC of 0 beside a range above 5 km is a glitch):
a range of 0 beside a SoC above 5% is not a reading, and neither is an absent one. The Overview keeps
the last range the car did report, as it keeps the last GPS fix; the stored rows from before are
repaired once.
"""
import sqlite3
from datetime import datetime, timezone

import capability_profile
import client
import db as D
import db_reader
import mqtt as M
import pytest
import recorder as R


@pytest.mark.parametrize("sig, km", [
    ({"1204": 100, "3260": 0}, None),            # #365: the C10's going-to-sleep frame
    ({"1204": 100, "3260": "0"}, None),
    ({"100003": "83.0", "3260": 0.0}, None),
    ({"1204": 100}, None),                       # absent is not zero
    ({"1204": 100, "3260": ""}, None),
    ({"1204": 3, "3260": 0}, 0.0),               # an empty battery: a real zero stays a zero
    ({"100003": "97.4", "3260": 426}, 426.0),
    ({"1204": 0, "3260": 0}, 0.0),
])
def test_the_rule(sig, km):
    assert capability_profile.battery_range_km(sig) == km


def _frame(range_km, soc=100):
    sig = {"1": int(datetime(2026, 10, 1, 5, 18, 51, tzinfo=timezone.utc).timestamp() * 1000),
           "1204": soc, "1010": 0, "3": 45.0, "2": 9.0, "1318": 3628}
    if range_km is not None:
        sig["3260"] = range_km
    return sig


def test_the_poller_stores_no_range_from_the_sleep_frame(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    rec = R.Recorder(db, vehicle_id=db.ensure_vehicle("TESTVIN", "C10"))
    data = client._parse_signal("TESTVIN", _frame(0))
    assert data.range_km is None
    rec.process(data)
    assert db._conn.execute("SELECT range_km FROM positions").fetchone()["range_km"] is None


def test_the_web_writer_follows_the_same_rule(tmp_path, monkeypatch):
    """Two writers, one field, one rule: the Refresh button stores a row as the poller does."""
    path = str(tmp_path / "w.db")
    D.Database(path).ensure_vehicle("TESTVIN", "C10")
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.save_fresh_signals(_frame(0))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT range_km FROM positions").fetchone()[0] is None


def test_the_overview_keeps_the_last_range_the_car_reported(tmp_path, monkeypatch):
    path = str(tmp_path / "o.db")
    db = D.Database(path)
    vid = db.ensure_vehicle("TESTVIN", "C10")
    for at, rng in (("2026-10-01T05:16:14+00:00", 426.0), ("2026-10-01T05:18:51+00:00", None)):
        db._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, range_km) "
                         "VALUES (?,?,100,?)", (vid, at, rng))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    status = db_reader.get_latest_status()
    assert status["range_km"] == 426.0
    assert status["range_stale"] is True


def test_the_stored_zeros_are_repaired_once(tmp_path):
    path = str(tmp_path / "r.db")
    db = D.Database(path)
    vid = db.ensure_vehicle("TESTVIN", "C10")
    rows = [("2026-09-22T01:45:22+00:00", 100.0, 0.0),    # the sleep frame
            ("2026-09-22T01:46:00+00:00", 3.0, 0.0),      # an empty battery
            ("2026-09-22T01:47:00+00:00", 80.0, 300.0)]
    db._conn.executemany("INSERT INTO positions (vehicle_id, recorded_at, soc, range_km) "
                         "VALUES (?,?,?,?)", [(vid, *r) for r in rows])
    db.set_setting("positions_zero_range_repair_v1", "")
    db._conn.commit()
    db.close()
    D.Database(path)                                      # the repair runs on start, once
    stored = sqlite3.connect(path).execute(
        "SELECT range_km FROM positions ORDER BY recorded_at").fetchall()
    assert [r[0] for r in stored] == [None, 0.0, 300.0]


class _Client:
    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    def publish(self, topic, payload=None, retain=False, **kw):
        self.sent.append((topic, payload))


def test_home_assistant_keeps_the_last_range_instead_of_unknown():
    """A retained message stays until the next one: publishing nothing keeps the last real range,
    where an empty payload would turn the sensor `unknown` at every sleep."""
    bridge = M.MqttService(broker="h", port=1883, discovery_enabled=False, get_setting=lambda *a, **k: "")
    bridge.client = _Client()
    data = client._parse_signal("TESTVIN", _frame(0))
    bridge._publish_sensors(data)
    assert not [t for t, _ in bridge.client.sent if t.endswith("/range")]
    data = client._parse_signal("TESTVIN", _frame(426))
    bridge._publish_sensors(data)
    assert [p for t, p in bridge.client.sent if t.endswith("/range")] == ["426.0"]


def test_the_poll_line_still_prints_without_a_range():
    """`Range %d km` with None raised inside the logger at every sleep frame."""
    from poll_cycle_fixture import poller_main
    main = poller_main("poller_main_range_text")
    assert main._range_text(None) == "—"
    assert main._range_text(426.0) == "426"
