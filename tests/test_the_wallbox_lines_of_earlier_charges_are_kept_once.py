"""The wallbox lines of the home charges from before the readings were kept are kept once, from Home Assistant.

After the update the poller keeps the wallbox's readings with every home charge, and the chart draws from them.
The charges from before have no readings of their own, and their lines would be gone with Home Assistant's
history, ten days after each. Once, after the update, Mate reads those lines from the history while it still
reaches them, power and counter, for every car, and keeps them like the readings since. A charge the history no
longer reaches keeps nothing, a charge that has readings is left alone, and a charge Home Assistant did not
answer for, on any of its readings, keeps nothing yet and is read again on a later start: done is recorded only
once every charge was read.
"""
import sqlite3

import pytest

pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db_reader
import ha_client
import main
from test_the_cable_shows_while_the_charger_holds import car  # noqa: F401  (car: fixture)

_read_history = ha_client.read_history          # the real one, for the test that answers at the transport

T = ["2026-10-10T20:00:00+00:00", "2026-10-10T20:00:30+00:00", "2026-10-10T20:01:00+00:00"]
EARLIER = ["2026-10-09T20:00:00+00:00", "2026-10-09T20:00:30+00:00", "2026-10-09T20:01:00+00:00"]
GONE = ["2026-09-20T20:00:00+00:00", "2026-09-20T20:01:00+00:00"]
OTHER_CAR = ["2026-10-08T20:00:00+00:00", "2026-10-08T20:00:30+00:00"]
POWER, ENERGY = "sensor.wb_power", "sensor.wb_energy"


def _history(eid, lo, hi):
    """Home Assistant's history: the power and the counter from 8 October on, nothing before (purged)."""
    if lo < OTHER_CAR[0]:
        return []
    if eid == POWER:
        return [(ha_client.epoch(lo), 7.0), (ha_client.epoch(lo) + 60, 7.2)]
    return [(ha_client.epoch(lo), 1000.0), (ha_client.epoch(lo) + 30, 1000.06)]


@pytest.fixture
def charges(car, monkeypatch):
    """Finished home charges: one with its readings kept, one from before with its history still in Home
    Assistant, one older than the history, one of a second car; and one away from home. The wallbox mapped;
    Home Assistant answers unless an entity is in `failing`."""
    for times in (T, EARLIER, GONE):
        for t in times:
            car.frame(recorded_at=t, soc=50, charging=1, plug_connected=1, charge_current_a=-17.5, charge_voltage_v=400.0)
    for t in OTHER_CAR:
        car.frame(vehicle_id=2, recorded_at=t, soc=50, charging=1, plug_connected=1, charge_current_a=-17.5, charge_voltage_v=400.0)
    with sqlite3.connect(db_reader.DB_PATH) as c:
        c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2, 'LVIN0000000000002', 'C10')")
        for cid, vid, times, where in ((1, 1, T, "HOME"), (2, 1, EARLIER, "HOME"), (3, 1, GONE, "HOME"),
                                       (4, 1, EARLIER, "AC"), (5, 2, OTHER_CAR, "HOME")):
            c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, location_type) VALUES (?,?,?,?,50,?)",
                      (cid, vid, times[0], times[-1], where))
        for t in T:
            c.execute("INSERT INTO ha_samples (vehicle_id, recorded_at, kind, value) VALUES (1, ?, 'wallbox_power_kw', 7.5)", (t,))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wallbox_enabled', '1')")
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wallbox_entities', ?)",
                  ('{"power": "%s", "energy": "%s"}' % (POWER, ENERGY),))
    failing = set()
    asked = []
    # The fixture pins every read to car 1; the sweep goes car by car through the scope, as the real reader honours it.
    monkeypatch.setattr(db_reader, "_current_vehicle_id",
                        lambda: (getattr(db_reader._read_vehicle_scope, "vehicle_id", None) or (1,))[0])
    monkeypatch.setattr(ha_client, "is_configured", lambda: True)
    monkeypatch.setattr(ha_client, "read_history", lambda eid, lo, hi: asked.append((eid, lo[:10])) or
                        (None if eid in failing else _history(eid, lo, hi)))
    return asked, failing


def _kept(vehicle_id=1):
    with sqlite3.connect(db_reader.DB_PATH) as c:
        return c.execute("SELECT recorded_at, kind, value FROM ha_samples WHERE vehicle_id = ? ORDER BY recorded_at, kind",
                         (vehicle_id,)).fetchall()


def _of(rows, times):
    return [r for r in rows if r[0] in times]


def test_the_lines_the_history_still_reaches_are_kept_and_the_rest_left_alone(charges):
    asked, _ = charges
    main._keep_earlier_wallbox_lines()

    assert _of(_kept(), EARLIER) == [
        (EARLIER[0], "wallbox_energy_kwh", 1000.0), (EARLIER[0], "wallbox_power_kw", 7.0),
        (EARLIER[1], "wallbox_energy_kwh", 1000.06), (EARLIER[1], "wallbox_power_kw", 7.0),
        (EARLIER[2], "wallbox_energy_kwh", 1000.06), (EARLIER[2], "wallbox_power_kw", 7.2)]
    assert _of(_kept(), T) == [(t, "wallbox_power_kw", 7.5) for t in T]      # left as kept
    assert not _of(_kept(), GONE)                                              # purged: nothing
    assert sorted(set(asked)) == [(ENERGY, "2026-09-20"), (ENERGY, "2026-10-08"), (ENERGY, "2026-10-09"),
                                  (POWER, "2026-09-20"), (POWER, "2026-10-08"), (POWER, "2026-10-09")]
    assert db_reader.get_setting("wallbox_lines_kept") == "1"


def test_every_car_is_read_whichever_the_sidebar_picked(charges):
    assert db_reader._current_vehicle_id() == 1
    main._keep_earlier_wallbox_lines()

    assert _of(_kept(2), OTHER_CAR) == [
        (OTHER_CAR[0], "wallbox_energy_kwh", 1000.0), (OTHER_CAR[0], "wallbox_power_kw", 7.0),
        (OTHER_CAR[1], "wallbox_energy_kwh", 1000.06), (OTHER_CAR[1], "wallbox_power_kw", 7.0)]


def test_a_charge_home_assistant_did_not_answer_for_is_read_again_whole(charges):
    """One reading refused (a 401, a 503, a timeout) keeps nothing of that charge: half a line kept would count
    as done next time, and the other half would be lost with the history."""
    asked, failing = charges
    failing.add(ENERGY)
    main._keep_earlier_wallbox_lines()
    assert not _of(_kept(), EARLIER) and not _of(_kept(2), OTHER_CAR)
    assert db_reader.get_setting("wallbox_lines_kept", "0") == "0"

    failing.clear()
    main._keep_earlier_wallbox_lines()
    assert len(_of(_kept(), EARLIER)) == 6 and len(_of(_kept(2), OTHER_CAR)) == 4    # both lines, once
    assert db_reader.get_setting("wallbox_lines_kept") == "1"


def test_no_answer_from_home_assistant_is_no_reading_but_an_empty_history_is(charges, monkeypatch):
    """Through the real `read_history`: a timeout and a 503 on the counter keep nothing and leave the sweep undone;
    an answer of no states (purged) is a reading of nothing, and the sweep is done."""
    answer = {"energy": TimeoutError("timed out")}
    power = [[{"last_changed": EARLIER[0], "state": "7.0", "attributes": {"unit_of_measurement": "kW"}},
              {"last_changed": EARLIER[2], "state": "7.2"}]]

    def request(path):
        if "wb_energy" in path and isinstance(answer["energy"], Exception):
            raise answer["energy"]
        return (200, power) if "wb_power" in path else answer["energy"]

    monkeypatch.setattr(ha_client, "read_history", _read_history)
    monkeypatch.setattr(ha_client, "_request", request)
    for answer["energy"] in (TimeoutError("timed out"), (503, "Service Unavailable")):
        main._keep_earlier_wallbox_lines()
        assert not _of(_kept(), EARLIER) and db_reader.get_setting("wallbox_lines_kept", "0") == "0"

    answer["energy"] = (200, [])
    main._keep_earlier_wallbox_lines()
    assert _of(_kept(), EARLIER) == [(EARLIER[0], "wallbox_power_kw", 7.0), (EARLIER[1], "wallbox_power_kw", 7.0),
                                     (EARLIER[2], "wallbox_power_kw", 7.2)]
    assert db_reader.get_setting("wallbox_lines_kept") == "1"


def test_once_done_nothing_is_asked_again(charges, monkeypatch):
    asked, _ = charges
    db_reader.set_setting("wallbox_lines_kept", "1")
    started = []
    monkeypatch.setattr(main.threading, "Thread", lambda **kw: started.append(kw) or type("T", (), {"start": lambda self: None})())
    main._maybe_keep_earlier_wallbox_lines()

    assert started == [] and asked == []
