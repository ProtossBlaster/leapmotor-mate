"""The chart of each tyre's daily pressure on the Vehicle page, as the server hands it out (D#483).

The page asks /api/tyre-history once, for 30 or 90 days. A day with no reading is a hole on the
axis, not a missing day and not a 0; with no history yet the card says so instead of drawing an
empty chart; without an outside temperature there is no temperature line.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")

import db_reader
from test_missed_charge_scan import _seed

NEW_KEYS = ("tyre_history_title", "tyre_history_empty", "tyre_history_min", "tyre_history_max",
            "tyre_history_90d")


def _client(tmp_path, monkeypatch, rows=(), lang="en"):
    """`rows` = [(days_ago, fl, fr, rl, rr, outside_temp), …], at noon UTC."""
    import main
    from starlette.testclient import TestClient
    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    db = _seed(tmp_path, monkeypatch)
    for k, v in (("setup_complete", "1"), ("timezone", "UTC"), ("language", lang)):
        db.set_setting(k, v)
    db_reader._lang_memo[0] = None
    noon = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    for ago, *vals in rows:
        db._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, tire_fl_bar, tire_fr_bar, tire_rl_bar,"
            " tire_rr_bar, outside_temp) VALUES (1,?,?,?,?,?,?)",
            ((noon - timedelta(days=ago)).isoformat(), *vals))
    db._conn.commit()
    return TestClient(main.app)


def _data(html):
    """The JSON the partial hands to the chart."""
    m = re.search(r'<script type="application/json" id="tyre-history-data">(.*?)</script>', html, re.S)
    assert m, "no chart data in the partial"
    return json.loads(m.group(1))


def test_the_partial_carries_the_days_and_the_values(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, [(1, 2.40, 2.5, 2.5, 2.5, 4.0), (1, 2.55, 2.5, 2.5, 2.5, 9.0)])
    r = c.get("/api/tyre-history")
    assert r.status_code == 200
    d = _data(r.text)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    i = d["days"].index(yesterday)
    assert (d["fl_min"][i], d["fl_max"][i]) == (2.40, 2.55)
    assert (d["temp_min"][i], d["temp_max"][i]) == (4.0, 9.0)


def test_a_day_without_readings_is_a_hole_on_the_axis(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, [(3, 2.5, 2.5, 2.5, 2.5, None), (1, 2.4, 2.5, 2.5, 2.5, None)])
    d = _data(c.get("/api/tyre-history").text)
    assert len(d["days"]) == 4                         # 3 days ago … today
    assert d["fl_min"] == [2.5, None, 2.4, None]


def test_no_history_says_so(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/api/tyre-history")
    assert r.status_code == 200
    assert "tyre-history-data" not in r.text
    assert "No tyre pressures in the last 30 days" in r.text


def test_no_temperature_no_temperature_line(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, [(1, 2.5, 2.5, 2.5, 2.5, None)])
    d = _data(c.get("/api/tyre-history").text)
    assert all(v is None for v in d["temp_min"] + d["temp_max"])
    assert 'data-series="temp"' not in c.get("/api/tyre-history").text


@pytest.mark.parametrize("asked, used", [("30", 30), ("90", 90), ("7", 30), ("abc", 30), ("", 30)])
def test_only_30_or_90_days(tmp_path, monkeypatch, asked, used):
    c = _client(tmp_path, monkeypatch, [(60, 2.5, 2.5, 2.5, 2.5, None), (1, 2.5, 2.5, 2.5, 2.5, None)])
    d = _data(c.get(f"/api/tyre-history?days={asked}").text)
    assert d["period"] == used
    assert (len(d["days"]) > 30) == (used == 90)


def test_the_vehicle_page_asks_for_the_chart_once(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch).get("/vehicle").text
    tag = re.search(r'<div[^>]*hx-get="api/tyre-history"[^>]*>', html)
    assert tag and 'hx-trigger="load"' in tag.group(0)


def test_every_new_key_is_in_every_locale():
    import pathlib
    for f in sorted(pathlib.Path(__file__).resolve().parent.parent.joinpath("web", "locales").glob("*.json")):
        keys = json.loads(f.read_text(encoding="utf-8"))["translations"]
        missing = [k for k in NEW_KEYS if not keys.get(k)]
        assert not missing, f"{f.name} misses {missing}"


def test_temperature_alone_is_no_history(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, [(3, None, None, None, None, 5.0), (1, None, None, None, None, 7.0)])
    r = c.get("/api/tyre-history")
    assert "tyre-history-data" not in r.text and "No tyre pressures in the last 30 days" in r.text


def test_thirty_empty_days_say_thirty_days_not_never(tmp_path, monkeypatch):
    """Readings 60 days ago and none since: nothing in the last 30 days, which is not «never»."""
    c = _client(tmp_path, monkeypatch, [(60, 2.5, 2.5, 2.5, 2.5, None)])
    assert "No tyre pressures in the last 30 days" in c.get("/api/tyre-history?days=30").text
    assert "tyre-history-data" in c.get("/api/tyre-history?days=90").text


def test_the_chart_stops_where_the_positions_retention_cuts(tmp_path, monkeypatch):
    """Past the retention only charging rows are kept (prune_positions): a day built from them is the
    hours plugged in, not the day. With a 20-day retention the 90 days chart shows 20 days."""
    c = _client(tmp_path, monkeypatch, [(40, 2.3, 2.5, 2.5, 2.5, None), (5, 2.5, 2.5, 2.5, 2.5, None)])
    import db_reader
    db_reader.set_setting("positions_retention_days", "20")
    d = _data(c.get("/api/tyre-history?days=90").text)
    assert 2.3 not in d["fl_min"] and len(d["days"]) <= 21
