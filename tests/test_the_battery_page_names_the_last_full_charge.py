"""The Battery page says when the battery was last charged to 100% (D#490, @Tommy73LMB05).

An LFP battery is charged to 100% now and then so the cells balance and the SoC is measured right.
The page shows the last charge that ended at 100%, from the charges Mate already records. It sets
no threshold of its own: the manuals and the owners do not agree on how often.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from events_fixture import Car, web
    car = Car(tmp_path)
    client = web(car, monkeypatch, zone="Europe/Rome", lang="it")
    return car, client


def _charge(car, ended, end_soc, vid=None, note=None):
    started = (datetime.fromisoformat(ended) - timedelta(hours=3)).isoformat() if ended else "2026-11-01T10:00:00+00:00"
    car.db._conn.execute(
        "INSERT INTO charges (vehicle_id,started_at,ended_at,start_soc,end_soc,note) VALUES (?,?,?,?,?,?)",
        (vid or car.vid, started, ended, 40, end_soc, note))
    car.db._conn.commit()


def test_none_yet(app):
    import db_reader
    car, client = app
    _charge(car, "2026-11-10T06:00:00+00:00", 99.9)
    assert db_reader.get_last_full_charge() is None
    assert "Nessuna ricarica al 100% registrata" in client.get("/battery").text


def test_the_latest_closed_charge_that_ended_at_100(app):
    import db_reader
    car, client = app
    _charge(car, "2026-11-04T06:00:00+00:00", 100.0)
    _charge(car, "2026-11-16T01:43:59+00:00", 100.0, note="Casa")
    _charge(car, "2026-11-18T06:00:00+00:00", 80.0)
    _charge(car, None, 100.0)                       # still open
    last = db_reader.get_last_full_charge()
    assert last["ended_at"] == "2026-11-16T01:43:59+00:00"


def test_another_cars_charge_is_not_this_ones(app):
    import db_reader
    car, client = app
    other = car.db.ensure_vehicle("VINOTHERCAR000001", "T03")
    _charge(car, "2026-11-16T01:43:59+00:00", 100.0, vid=other)
    assert db_reader.get_last_full_charge() is None


def test_the_page_shows_its_day_and_how_long_ago(app, monkeypatch):
    car, client = app
    ended = (datetime.now(timezone.utc) - timedelta(days=4)).replace(microsecond=0)
    _charge(car, ended.isoformat(), 100.0)
    page = client.get("/battery").text
    assert "Ultima ricarica al 100%" in page
    assert "4 g fa" in page
