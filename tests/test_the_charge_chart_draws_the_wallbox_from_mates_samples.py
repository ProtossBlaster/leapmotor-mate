"""The wallbox's line on a charge's chart is drawn from the readings Mate kept, not read back from Home Assistant.

A home charge whose readings the poller kept (see test_the_wallbox_is_read_into_mates_own_samples) draws its
wallbox line from them, on the chart of a finished charge and on the chart of the one in progress; Home
Assistant's history is not asked. A charge from before Mate kept them draws from that history as before, while
Home Assistant's recorder still has it.
"""
import re
import sqlite3

import pytest

pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db_reader
import ha_client
from test_the_cable_shows_while_the_charger_holds import car  # noqa: F401  (car: fixture)

T = ["2026-10-10T20:00:00+00:00", "2026-10-10T20:00:30+00:00", "2026-10-10T20:01:00+00:00", "2026-10-10T20:01:30+00:00"]


def _charge(car, *, ended):
    """A home charge over T, the car reading 7 kW; the wallbox's readings at the first three polls."""
    for i, t in enumerate(T):
        car.frame(recorded_at=t, soc=50 + i, charging=1, plug_connected=1, charge_current_a=-17.5, charge_voltage_v=400.0)
    with sqlite3.connect(db_reader.DB_PATH) as c:
        c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, location_type) VALUES (1,1,?,?,50,'HOME')",
                  (T[0], T[-1] if ended else None))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wallbox_enabled', '1')")
        for t, kw in zip(T, (7.4, 7.5, 7.6)):
            c.execute("INSERT INTO ha_samples (vehicle_id, recorded_at, kind, value) VALUES (1, ?, 'wallbox_power_kw', ?)", (t, kw))


def _wallbox_line(html):
    m = re.search(r"wallbox:\s*\{.*?data: col\((\[[^\]]*\])\)", html, re.DOTALL)
    return m.group(1).replace(" ", "") if m else None


def test_a_finished_charge_draws_the_line_from_the_kept_readings(car, monkeypatch):
    _charge(car, ended=True)
    monkeypatch.setattr(ha_client, "get_history", lambda *a: pytest.fail("Home Assistant's history was asked"))
    assert _wallbox_line(car.get("/api/charge/1/power-chart")) == "[7.4,7.5,7.6,7.6]"   # the last reading holds


def test_the_charge_in_progress_draws_it_from_them_too(car, monkeypatch):
    _charge(car, ended=False)
    monkeypatch.setattr(ha_client, "get_history", lambda *a: pytest.fail("Home Assistant's history was asked"))
    assert _wallbox_line(car.get("/api/charging-chart")) == "[7.4,7.5,7.6,7.6]"


def test_a_charge_from_before_the_readings_were_kept_draws_from_home_assistants_history(car, monkeypatch):
    _charge(car, ended=True)
    with sqlite3.connect(db_reader.DB_PATH) as c:
        c.execute("DELETE FROM ha_samples")
    asked = []
    monkeypatch.setattr(ha_client, "is_configured", lambda: True)
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {"power": "sensor.wallbox_power"})
    monkeypatch.setattr(ha_client, "recently_unreachable", lambda: False)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: asked.append((eid, lo, hi)) or
                        [(ha_client.epoch(T[0]), 7.0), (ha_client.epoch(T[2]), 7.2)])
    assert _wallbox_line(car.get("/api/charge/1/power-chart")) == "[7.0,7.0,7.2,7.2]"
    assert asked == [("sensor.wallbox_power", T[0], T[-1])]
