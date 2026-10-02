"""A frame without a charge level is not a reading, for the web as for the poller.

Since 1.21.4 the poller refuses a status that carries no usable SoC — neither `100003` nor `1204`, or
a SoC of 0 beside a battery range above 5 km — as no live data: `client.get_status` raises
`EmptyStatusError` and no position is stored. The web stores positions too, for the Refresh button and
for the check after a command, and its writer never learned the rule: `save_fresh_signals` kept
`sigf("100003") or sigf("1204")`, unchanged since 1.0.0, and stored 0 % for a SoC the car did not
send. Found in a bundle on #67: a position at 0 % that the poller's log has no line for.

One rule for the two writers of `positions.soc`, as for the range (#365): it lives in
`capability_profile`, one copy per process, and both writers read it.

The positions already stored that way go once, at the first start. 4.7.12's repair reaches back to
4.7.11 only, and 1.21.4's nulled the rows of that day only.
"""
import sqlite3
from types import SimpleNamespace

import pytest

import client
import db as D
import db_reader

VIN = "TESTVIN"

NOT_READINGS = [
    {"1010": 0, "1318": 3628, "3260": 300},          # no charge level at all
    {"1010": 0},                                     # nor a range
    {"1204": None, "3260": 300},                     # sent, as null
    {"1204": 0, "3260": 300},                        # 0 % beside 300 km: a partial read
    {"100003": "0.0", "1204": 62, "3260": 300},      # the precise SoC is the one the rule reads
]

READINGS = [
    ({"1204": 62, "1318": 3628, "3260": 300}, 62.0),
    ({"100003": "61.5", "1204": 62}, 61.5),
    ({"1204": 0, "3260": 0}, 0.0),                   # an empty battery is a reading
    ({"1204": 0}, 0.0),
    ({"1204": 0, "3260": 5}, 0.0),                   # 5 km is not above 5
    ({"1204": 3, "3260": 12}, 3.0),
]


@pytest.fixture
def stored_soc(tmp_path, monkeypatch):
    """The web's database, and what its positions hold."""
    path = str(tmp_path / "web.db")
    D.Database(path).ensure_vehicle(VIN, "C10")
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    def read():
        con = sqlite3.connect(path)
        try:
            return [r[0] for r in con.execute("SELECT soc FROM positions ORDER BY id")]
        finally:
            con.close()
    return read


@pytest.mark.parametrize("sig", NOT_READINGS)
def test_the_web_stores_nothing_from_a_frame_without_a_charge_level(sig, stored_soc):
    db_reader.save_fresh_signals(dict(sig))
    assert stored_soc() == []


@pytest.mark.parametrize("sig, soc", READINGS)
def test_the_web_stores_a_reading_with_its_charge_level(sig, soc, stored_soc):
    db_reader.save_fresh_signals(dict(sig))
    assert stored_soc() == [soc]


def _poller(sig, monkeypatch):
    import session_share
    monkeypatch.setattr(session_share, "ensure_account_cert", lambda api: True)
    poller = object.__new__(client.LeapmotorMateClient)
    poller._api, poller._named_mode_logged = None, False
    poller._vehicle = SimpleNamespace(vin=VIN, car_type="C10")
    poller._raw_status = lambda vehicle=None: {"data": {"signal": dict(sig)}}
    return poller


@pytest.mark.parametrize("sig", NOT_READINGS)
def test_the_poller_refuses_the_same_frames(sig, monkeypatch):
    with pytest.raises(client.EmptyStatusError):
        _poller(sig, monkeypatch).get_status()


@pytest.mark.parametrize("sig, soc", READINGS)
def test_the_poller_reads_the_same_charge_level(sig, soc, monkeypatch):
    assert _poller(sig, monkeypatch).get_status().soc == soc


# ── The positions already stored at 0 % ───────────────────────────────────────
# @Gr1m214's T03 (#67), 1 October: the poller read 39 % at 09:18:38Z, then got no live data from
# 09:19Z on; at 11:01:24Z, a minute before Mate restarted on 4.7.8, a position at 0 % was stored that
# no poll wrote. His day read 43 → 0 %, his stop 39 points down in 2.9 hours. Its odometer and range
# are not in his bundle, so each shape a frame without a SoC could leave is tried.
REPAIR = "positions_zero_soc_repair_v1"


def _after_a_restart(tmp_path, rows, cars=("TESTVIN",)):
    """The socs left, per car, once Mate restarts on a database holding `rows`
    (car index, recorded_at, soc, odometer_km, range_km)."""
    path = str(tmp_path / "z.db")
    db = D.Database(path)
    vids = [db.ensure_vehicle(vin, "T03") for vin in cars]
    db._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, odometer_km, range_km) VALUES (?,?,?,?,?)",
        [(vids[car], at, soc, odo, rng) for car, at, soc, odo, rng in rows])
    db.set_setting(REPAIR, "")            # an install that has not run it yet
    db._conn.commit()
    db.close()
    D.Database(path).close()              # the repair runs on start
    con = sqlite3.connect(path)
    try:
        return [[r[0] for r in con.execute(
            "SELECT soc FROM positions WHERE vehicle_id = ? ORDER BY recorded_at", (vid,))] for vid in vids]
    finally:
        con.close()


@pytest.mark.parametrize("odometer", [None, 0.0, 16698.0])
def test_a_zero_the_car_never_read_is_removed(tmp_path, odometer):
    rows = [(0, "2026-10-01T09:18:07+00:00", 39.0, 16698.0, 138.0),
            (0, "2026-10-01T09:18:38+00:00", 39.0, 16698.0, 138.0),
            (0, "2026-10-01T11:01:24+00:00", 0.0, odometer, None)]
    assert _after_a_restart(tmp_path, rows) == [[39.0, 39.0]]


def test_a_zero_beside_a_range_is_removed(tmp_path):
    """The poller's own rule, on a stored row: nothing before it to compare with."""
    assert _after_a_restart(tmp_path, [(0, "2026-09-01T10:00:00+00:00", 0.0, 9000.0, 120.0)]) == [[]]


def test_every_zero_after_a_reading_is_removed(tmp_path):
    rows = [(0, "2026-09-01T10:00:00+00:00", 60.0, 9000.0, 210.0),
            (0, "2026-09-01T10:05:00+00:00", 0.0, None, None),
            (0, "2026-09-01T10:06:00+00:00", 0.0, None, None)]
    assert _after_a_restart(tmp_path, rows) == [[60.0]]


def test_a_battery_that_ran_down_keeps_its_zero(tmp_path):
    """Parked at 1 %, it loses the last point overnight: the odometer stands still, the zero is real."""
    rows = [(0, "2026-09-01T10:00:00+00:00", 3.0, 9000.0, 9.0),
            (0, "2026-09-01T10:10:00+00:00", 1.0, 9006.0, 2.0),
            (0, "2026-09-02T06:00:00+00:00", 0.0, 9006.0, 0.0)]
    assert _after_a_restart(tmp_path, rows) == [[3.0, 1.0, 0.0]]


def test_a_zero_after_a_drive_nobody_saw_is_kept(tmp_path):
    """Mate down for a day, the car driven empty: the odometer moved, so the zero can be real."""
    rows = [(0, "2026-09-01T10:00:00+00:00", 50.0, 9000.0, 180.0),
            (0, "2026-09-02T18:00:00+00:00", 0.0, 9230.0, 0.0)]
    assert _after_a_restart(tmp_path, rows) == [[50.0, 0.0]]


def test_another_cars_reading_is_no_comparison(tmp_path):
    rows = [(0, "2026-09-01T10:00:00+00:00", 60.0, 9000.0, 210.0),
            (1, "2026-09-01T10:05:00+00:00", 0.0, None, None)]
    assert _after_a_restart(tmp_path, rows, cars=("VINA", "VINB")) == [[60.0], [0.0]]


def test_the_repair_runs_once(tmp_path):
    path = str(tmp_path / "z.db")
    db = D.Database(path)                 # a new install: it has run, on nothing
    vid = db.ensure_vehicle("TESTVIN", "T03")
    db._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, odometer_km, range_km) VALUES (?,?,?,?,?)",
        [(vid, "2026-09-01T10:00:00+00:00", 60.0, 9000.0, 210.0),
         (vid, "2026-09-01T10:05:00+00:00", 0.0, None, None)])
    db._conn.commit()
    db.close()
    D.Database(path).close()
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 2
    finally:
        con.close()
