"""While a charge runs, the Charges page carries its chart among the cards at its top: the chart a
finished charge gets under its card, live, with the time the charge began and its SoC so far. The
card is there only for a charge in progress,
and only once it has two readings to draw; a finished charge leaves the panel empty, and its chart is
where it always was, under the card. The wallbox's line waits for the card too: the charge's type is
sure only once it has ended, and the panel does not ask Home Assistant for the meter on every poll.
"""
import datetime as dt
from types import SimpleNamespace

import db as D
import db_reader
import main
import pytest
from starlette.testclient import TestClient

VIN = "LVIN0000000000001"
PLUGGED_IN = dt.datetime(2026, 10, 2, 6, 13, tzinfo=dt.timezone.utc)


@pytest.fixture
def car(tmp_path, monkeypatch):
    """A car plugged in at 06:13 UTC from 77.1 %; `car.polls(n)` records the next n polls of the charge."""
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
    yield SimpleNamespace(polls=polls, db=c)
    c.close()


def test_the_page_draws_the_charge_in_progress(car):
    car.polls(5)
    page = TestClient(main.app).get("/charges").text
    live = page.split('id="charging-chart-panel"')[1].split("<script>")[0]
    assert 'id="pc-9"' in live, "the chart of the running charge is in the panel"
    assert "LIVE" in live and "Charging data" in live
    assert "06:13" in live and "77.1%" in live and "77.5%" in live and "+0.4%" in live, \
        "when it began, the SoC it began from, the SoC now and the gain so far"


def test_a_charge_with_one_reading_has_no_chart_yet(car):
    car.polls(1)
    assert TestClient(main.app).get("/api/charging-chart").text.strip() == ""


def test_a_finished_charge_leaves_the_panel_empty(car):
    car.polls(5)
    car.db.execute("UPDATE charges SET ended_at = ?, end_soc = 77.5 WHERE id = 9",
                   ((PLUGGED_IN + dt.timedelta(minutes=2)).isoformat(),))
    car.db.commit()
    assert TestClient(main.app).get("/api/charging-chart").text.strip() == ""
    assert 'id="pc-9"' not in TestClient(main.app).get("/charges").text
    assert 'id="pc-9"' in TestClient(main.app).get("/api/charge/9/power-chart").text, \
        "the finished charge's chart is where it always was, under its card"


def test_the_live_chart_does_not_ask_for_the_wallbox(car, monkeypatch):
    """Even a charge born HOME (the "I always charge at home" setting) gets no wallbox line while it
    runs: the setting cannot tell a public AC charger from the wallbox, and the meter's history would
    be read from Home Assistant on every poll of the panel."""
    car.db.execute("UPDATE charges SET location_type = 'HOME' WHERE id = 9")
    car.db.commit()
    car.polls(5)
    monkeypatch.setattr(main, "_wallbox_overlay",
                        lambda *a: pytest.fail("the chart of a running charge asked for the wallbox's history"))
    polled = TestClient(main.app).get("/api/charging-chart").text
    assert 'id="pc-9"' in polled and "data: col([])" in polled.split("wallbox:")[1].split("\n")[0]
