"""A trip's detail gives the SoC and the odometer as one line each, start → end, instead of five
rows; each line carries its change in brackets, the SoC's signed. An end that is not known prints a dash in
its place, so a lone figure is never mistaken for the other end — and a missing start odometer is a
dash, not "0 km".
"""
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

START = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)


def _row(tmp_path, monkeypatch, label, lang="en", **trip):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    for key, value in (("is_reev", "0"), ("timezone", "UTC"), ("setup_complete", "1"), ("language", lang)):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    values = {"start_soc": 80.0, "end_soc": 77.0, "start_odometer_km": 12000.0, "end_odometer_km": 12097.0}
    values.update(trip)
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc,"
              " end_soc, start_odometer_km, end_odometer_km) VALUES (1,1,?,?,97.0,70,?,?,?,?)",
              (START.isoformat(), (START + timedelta(minutes=70)).isoformat(), values["start_soc"],
               values["end_soc"], values["start_odometer_km"], values["end_odometer_km"]))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    html = TestClient(main.app).get("/trips/1").text
    row = re.search(r">" + re.escape(label) + r"</span>(.*?)</div>", html, re.DOTALL).group(1)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row).replace("&nbsp;", " ")).strip()


def test_soc_start_to_end_with_its_change(tmp_path, monkeypatch):
    assert _row(tmp_path, monkeypatch, "SOC") == "80.0% → 77.0% (-3.0%)"


def test_a_pack_that_ended_fuller_says_plus(tmp_path, monkeypatch):
    """A range extender's generator can put back more than the drive took."""
    assert _row(tmp_path, monkeypatch, "SOC", start_soc=50.0, end_soc=52.5) == "50.0% → 52.5% (+2.5%)"


@pytest.mark.parametrize("start, end, line", [(80.4, 77.2, "80,4% → 77,2% (-3,2%)"),
                                               (50.0, 52.5, "50,0% → 52,5% (+2,5%)")])
def test_the_change_writes_its_decimals_like_the_two_ends(tmp_path, monkeypatch, start, end, line):
    """In a language with a decimal comma the bracket follows it too, not only the two ends."""
    assert _row(tmp_path, monkeypatch, "SOC", lang="pl", start_soc=start, end_soc=end) == line


def test_one_end_unknown_gives_no_change(tmp_path, monkeypatch):
    assert _row(tmp_path, monkeypatch, "SOC", end_soc=None) == "80.0% → —"


def test_odometer_start_to_end_with_the_distance(tmp_path, monkeypatch):
    assert re.fullmatch(r"12\D?000 km → 12\D?097 km \(97 km\)", _row(tmp_path, monkeypatch, "Odometer"))


def test_a_missing_end_is_a_dash_in_its_place(tmp_path, monkeypatch):
    assert re.fullmatch(r"12\D?000 km → —", _row(tmp_path, monkeypatch, "Odometer", end_odometer_km=None))


def test_a_missing_start_odometer_is_not_zero(tmp_path, monkeypatch):
    assert re.fullmatch(r"— → 12\D?097 km", _row(tmp_path, monkeypatch, "Odometer", start_odometer_km=None))
