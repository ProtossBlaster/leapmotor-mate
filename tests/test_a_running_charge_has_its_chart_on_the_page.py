"""While a charge runs, the Charges page carries its chart among the cards at its top: the chart a
finished charge gets under its card, live, with the time the charge began and its SoC so far. The
card is there only for a charge in progress,
and only once it has two readings to draw; a finished charge leaves the panel empty, and its chart is
where it always was, under the card. The wallbox's line is drawn while the charge runs when the charge
is at home: typed HOME at its start, or the wallbox delivering power while the car is plugged in.
Either chart reads the wallbox's history off the event loop, so a Home Assistant out of reach holds
that chart alone, never every other request of the app.
"""
import asyncio
import datetime as dt
import re
from types import SimpleNamespace

import db as D
import db_reader
import main
import pytest
from starlette.testclient import TestClient

VIN = "LVIN0000000000001"
# Three minutes ago, so the polls the fixture records are fresh: a card is LIVE only on fresh readings.
PLUGGED_IN = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)).replace(microsecond=0)


@pytest.fixture
def car(tmp_path, monkeypatch):
    """A car plugged in three minutes ago from 77.1 %; `car.polls(n)` records the next n polls of the charge."""
    path = str(tmp_path / "t.db")
    c = D.Database(path)._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    for key, value in (("timezone", "UTC"), ("setup_complete", "1"), ("language", "en")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO charges (id,vehicle_id,started_at,start_soc) VALUES (9,1,?,77.1)",
              (PLUGGED_IN.isoformat(),))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)

    done = 0

    def polls(n):
        nonlocal done
        for k in range(done, done + n):
            c.execute("INSERT INTO positions (vehicle_id,recorded_at,charging,charge_voltage_v,charge_current_a,soc,"
                      "remaining_charge_min) VALUES (1,?,1,430,-7,?,?)",
                      ((PLUGGED_IN + dt.timedelta(seconds=30 * k)).isoformat(), 77.1 + 0.1 * k, 200 - k))
        done += n
        c.commit()

    def frame(**cols):
        """One more poll with the given columns, half a minute after the last."""
        nonlocal done
        at = PLUGGED_IN + dt.timedelta(seconds=30 * done)
        cols = {"vehicle_id": 1, "recorded_at": at.isoformat(), "soc": 77.1 + 0.1 * done, **cols}
        c.execute(f"INSERT INTO positions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
        done += 1
        c.commit()
    yield SimpleNamespace(polls=polls, frame=frame, db=c)
    c.close()


def test_the_page_draws_the_charge_in_progress(car):
    car.polls(5)
    page = TestClient(main.app).get("/charges").text
    live = page.split('id="charging-chart-panel"')[1].split("<script>")[0]
    assert 'id="pc-9"' in live, "the chart of the running charge is in the panel"
    assert "LIVE" in live and "Charging data" in live
    assert PLUGGED_IN.strftime("%H:%M") in live and "77.1%" in live and "77.5%" in live and "+0.4%" in live, \
        "when it began, the SoC it began from, the SoC now and the gain so far"


def test_a_charge_with_one_reading_has_no_chart_yet(car):
    car.polls(1)
    assert TestClient(main.app).get("/api/charging-chart").text.strip() == ""


def test_an_open_row_is_not_a_charge_while_the_car_says_otherwise(car):
    """The row stays open when the state machine never closes a session the car has left; the card
    follows the car's word, as the charging status panel does, not the row."""
    car.polls(5)
    car.frame(charging=0, plug_connected=0)
    assert TestClient(main.app).get("/api/charging-chart").text.strip() == ""


def test_a_car_out_of_touch_shows_the_age_of_its_frame_instead_of_live(car):
    """The cloud re-serves the last frame of a car that cannot reach it: the chart remains as a record
    of the session, the LIVE badge gives way to the age of the frame, as the Overview shows it."""
    car.polls(5)
    stamped = PLUGGED_IN + dt.timedelta(seconds=150) - dt.timedelta(minutes=20)   # the car's clock, 20 min behind the row
    car.frame(charging=1, charge_voltage_v=430, charge_current_a=-7, frame_ts=int(stamped.timestamp() * 1000))
    polled = TestClient(main.app).get("/api/charging-chart").text
    assert 'id="pc-9"' in polled, "the chart remains: the last available reading reported charging"
    card = polled.split("<script>")[0]
    assert "LIVE" not in card and "isn’t reaching the cloud" in card
    assert re.search(r"\d+[mhd] ago</span>", card), "the frame's age, in the reader's words, where LIVE was"


def test_readings_that_stopped_coming_take_live_down_too(car):
    """When the rows stop, the frame and the row age together and nothing lags anything: the age of
    the last reading has to speak for itself."""
    car.polls(5)
    for table, col in (("positions", "recorded_at"), ("charges", "started_at")):   # the whole charge, 20 min earlier
        for rid, at in car.db.execute(f"SELECT id, {col} FROM {table}").fetchall():
            moved = dt.datetime.fromisoformat(at) - dt.timedelta(minutes=20)
            car.db.execute(f"UPDATE {table} SET {col} = ? WHERE id = ?", (moved.isoformat(), rid))
    car.db.commit()
    card = TestClient(main.app).get("/api/charging-chart").text.split("<script>")[0]
    assert 'id="pc-9"' in card and "LIVE" not in card
    assert re.search(r"2\dm ago</span>", card), "the age of the last reading where LIVE was"
    assert "reaching the cloud" not in card, "nothing says the car lost the cloud: nobody knows why the rows stopped"


def test_a_finished_charge_leaves_the_panel_empty(car):
    car.polls(5)
    car.db.execute("UPDATE charges SET ended_at = ?, end_soc = 77.5 WHERE id = 9",
                   ((PLUGGED_IN + dt.timedelta(minutes=2)).isoformat(),))
    car.db.commit()
    assert TestClient(main.app).get("/api/charging-chart").text.strip() == ""
    assert 'id="pc-9"' not in TestClient(main.app).get("/charges").text
    assert 'id="pc-9"' in TestClient(main.app).get("/api/charge/9/power-chart").text, \
        "the finished charge's chart is where it always was, under its card"


def _wallbox(monkeypatch, car, power_kw):
    """A wallbox mapped in Home Assistant, delivering `power_kw` now and 3.1 kW all along."""
    import ha_client
    car.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wallbox_enabled', '1')")
    car.db.commit()
    monkeypatch.setattr(ha_client, "is_configured", lambda: True)
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {"power": "sensor.wallbox_power"})
    monkeypatch.setattr(ha_client, "get_live", lambda: {"configured": True, "power_kw": power_kw})
    monkeypatch.setattr(ha_client, "get_history",
                        lambda entity, start, end: [(ha_client.epoch(start) - 1, 3.1)])


def _wallbox_line(polled):
    return polled.split("wallbox:")[1].split("\n")[0]


def test_a_charge_at_home_draws_the_wallbox_beside_the_car(car, monkeypatch):
    """Typed HOME at its start, by a place recognised by GPS or the always-at-home setting."""
    car.db.execute("UPDATE charges SET location_type = 'HOME' WHERE id = 9")
    car.db.commit()
    car.polls(5)
    _wallbox(monkeypatch, car, power_kw=0)
    assert "data: col([3.1, 3.1, 3.1, 3.1, 3.1])" in _wallbox_line(TestClient(main.app).get("/api/charging-chart").text)


def test_a_wallbox_delivering_power_to_the_plugged_in_car_is_home(car, monkeypatch):
    """An untyped charge is at home when the wallbox delivers power while the car is plugged in."""
    car.polls(4)
    car.frame(charging=1, plug_connected=1, charge_voltage_v=430, charge_current_a=-7)
    _wallbox(monkeypatch, car, power_kw=3.1)
    assert "data: col([3.1, 3.1, 3.1, 3.1, 3.1])" in _wallbox_line(TestClient(main.app).get("/api/charging-chart").text)


def test_a_place_recognised_as_public_outranks_the_wallbox(car, monkeypatch):
    """A charge the GPS placed at a public charger gets no wallbox line even while the home wallbox
    delivers power: that power is another car's."""
    import ha_client
    car.db.execute("UPDATE charges SET location_type = 'HPC', charging_place_source = 'gps' WHERE id = 9")
    car.db.commit()
    car.polls(4)
    car.frame(charging=1, plug_connected=1, charge_voltage_v=430, charge_current_a=-7)
    _wallbox(monkeypatch, car, power_kw=3.1)
    monkeypatch.setattr(ha_client, "get_history",
                        lambda *a: pytest.fail("the home wallbox's history was read for a charge at a public charger"))
    polled = TestClient(main.app).get("/api/charging-chart").text
    assert 'id="pc-9"' in polled and "data: col([])" in _wallbox_line(polled)


def test_an_idle_wallbox_says_the_charge_is_elsewhere(car, monkeypatch):
    """Plugged in somewhere else: the home wallbox delivers nothing, so its history is not even read."""
    import ha_client
    car.polls(4)
    car.frame(charging=1, plug_connected=1, charge_voltage_v=430, charge_current_a=-7)
    _wallbox(monkeypatch, car, power_kw=0)
    monkeypatch.setattr(ha_client, "get_history",
                        lambda *a: pytest.fail("the home wallbox's history was read for a charge elsewhere"))
    polled = TestClient(main.app).get("/api/charging-chart").text
    assert 'id="pc-9"' in polled and "data: col([])" in _wallbox_line(polled)


@pytest.mark.parametrize("route", ["/api/charging-chart", "/api/charge/9/power-chart",
                                   "/api/wallbox/compare-chart?charge_id=9"])
def test_the_wallbox_history_is_read_off_the_event_loop(car, monkeypatch, route):
    import ha_client
    car.db.execute("UPDATE charges SET location_type = 'HOME' WHERE id = 9")
    car.db.commit()
    car.polls(5)
    _wallbox(monkeypatch, car, power_kw=0)
    on_the_loop = []

    def history(entity, start, end):
        try:
            on_the_loop.append(asyncio.get_running_loop())
        except RuntimeError:
            pass
        return [(ha_client.epoch(start) - 1, 3.1)]

    monkeypatch.setattr(ha_client, "get_history", history)
    assert "3.1" in _wallbox_line(TestClient(main.app).get(route).text) and not on_the_loop
