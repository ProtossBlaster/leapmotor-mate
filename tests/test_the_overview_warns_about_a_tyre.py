"""The Overview says when a tyre needs a look, and leads to the Vehicle page (D#483).

@michapr asked for it beside the A/C target: a tyre going down should be seen without opening the
Vehicle page. The Overview gives one word for all four tyres, the worst one, by the same bands the
Vehicle page colours each tyre with: the car's own low-pressure alarm or under 2.0 bar is too low,
under 2.3 low, up to 3.0 normal, up to 3.3 high, above too high.

The Overview refreshes every 30 seconds, so it cannot ask the cloud as the Vehicle page does: it
reads the last pressures the poller stored, and the car's alarm flags are stored beside them.
A car that never sent a pressure has no row; a tyre it did not send is left out, never read as 0.
"""
import pytest

import client
import db_reader
from test_missed_charge_scan import _seed

_BASE = {"2646": 250, "2653": 250, "2660": 250, "2667": 250}


@pytest.mark.parametrize("bar, alarm, status", [
    (2.5, 0, "normal"), (2.3, 0, "normal"), (3.0, 0, "normal"),
    (2.29, 0, "low"), (2.0, 0, "low"), (1.99, 0, "too_low"),
    (2.5, 1, "too_low"),                                  # the car's own alarm wins over the number
    (3.01, 0, "high"), (3.3, 0, "high"), (3.31, 0, "too_high"),
    (None, 0, None), (None, 1, None), (2.5, None, "normal"),
])
def test_one_rule_for_a_tyre(bar, alarm, status):
    assert db_reader.tyre_status(bar, alarm) == status


def test_the_poller_keeps_each_alarm_on_its_own_wheel():
    vd = client._parse_signal("VIN", {**_BASE, "2641": 0, "2648": 1, "2655": 0, "2662": 0})
    assert (vd.tire_fl_alarm, vd.tire_fr_alarm, vd.tire_rl_alarm, vd.tire_rr_alarm) == (0, 1, 0, 0)


def test_an_alarm_the_car_did_not_send_is_none():
    vd = client._parse_signal("VIN", dict(_BASE))
    assert vd.tire_fl_alarm is None


def _store(db, **sig):
    vd = client._parse_signal("VIN", {**_BASE, "2641": 0, "2648": 0, "2655": 0, "2662": 0, **sig})
    db.save_position(1, vd)


def test_the_worst_tyre_is_the_word(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _store(db, **{"2660": 195})                 # rear left 1.95 bar
    w = db_reader.tyre_warning()
    assert w["status"] == "too_low" and w["tyres"] == ["rl"]


def test_the_car_s_alarm_is_stored_and_read(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _store(db, **{"2648": 1})                   # front right, alarm on at 2.5 bar
    w = db_reader.tyre_warning()
    assert w["status"] == "too_low" and w["tyres"] == ["fr"]


def test_four_normal_tyres_say_normal(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _store(db)
    assert db_reader.tyre_warning() == {"status": "normal", "tyres": []}


def test_a_low_and_a_too_high_tyre_lead_with_the_red_one(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _store(db, **{"2646": 210, "2667": 340})    # front left low (amber), rear right too high (red)
    w = db_reader.tyre_warning()
    assert w["status"] == "too_high" and w["tyres"] == ["rr"]


def test_no_pressure_no_row(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    db.save_position(1, client._parse_signal("VIN", {}))
    assert db_reader.tyre_warning() is None


def test_a_poll_that_missed_the_tyres_keeps_the_last_ones(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _store(db, **{"2660": 195})
    db.save_position(1, client._parse_signal("VIN", {}))
    assert db_reader.tyre_warning()["status"] == "too_low"


def test_a_tyre_the_car_did_not_send_is_left_out(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    sig = {"2646": 250, "2653": 250, "2660": 250}           # no rear right
    db.save_position(1, client._parse_signal("VIN", sig))
    assert db_reader.tyre_warning() == {"status": "normal", "tyres": []}


def test_the_web_writer_stores_the_alarm_too(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({**_BASE, "1204": 60, "2641": 1, "2648": 0, "2655": 0, "2662": 0},
                                  "VIN1")
    w = db_reader.tyre_warning()
    assert w["status"] == "too_low" and w["tyres"] == ["fl"]


def _card(tmp_path, monkeypatch, **sig):
    pytest.importorskip("fastapi")
    import main
    from starlette.testclient import TestClient
    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    db = _seed(tmp_path, monkeypatch)
    db.set_setting("setup_complete", "1")
    db.set_setting("language", "en")
    db_reader._lang_memo[0] = None
    if sig is not None:
        _store(db, **sig)
    return TestClient(main.app).get("/api/status-card").text


def _row(html):
    marker = 'id="tyre-warning"'
    return html.split(marker, 1)[1].split("</a>", 1)[0] if marker in html else None


def test_the_overview_row_links_to_the_vehicle_page(tmp_path, monkeypatch):
    html = _card(tmp_path, monkeypatch, **{"2660": 195})
    row = _row(html)
    assert row is not None and "Too low" in row and "text-red-400" in row
    assert 'data-tip="Rear left"' in html, "the wheel is named, in the reader's language, in the tip"
    assert '<a href="vehicle"' in html.split('id="tyre-warning"', 1)[0][-200:]


def test_the_overview_says_normal_in_green(tmp_path, monkeypatch):
    row = _row(_card(tmp_path, monkeypatch))
    assert row is not None and "Normal" in row and "text-emerald-400" in row
