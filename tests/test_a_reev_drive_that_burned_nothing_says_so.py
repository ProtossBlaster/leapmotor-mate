"""A range-extender drive that burned nothing says "0 L", not nothing at all.

Silvio, 29/09/2026, looking at @ebagnoli's own history on a production build: *«mi sfugge qualcosa
perché gli altri viaggi non ci sono info?»* — ten trips on 30 July, two carrying litres and eight
blank. The eight are not unanswered. On 79 of his 150 trips the car's own millilitre counter reads
the IDENTICAL value at the start and at the end of the drive — measured, all 79 exactly 0.0, none
in the 0–5 mL band — and on 33 more the cloud's `driveReevOil` is 0.0. That is a measurement saying
"the generator never ran", and the page printed it exactly like the 25 trips where the tank was
never read at all.

Two different facts under one blank is the defect. So a measured zero prints `0 L` and the words for
"all electric", and the blank (`—` on the detail page) goes back to meaning what it says: we do not
know.

🔑 What is NOT a measured zero, and must stay unknown:
  · the percentage gauge reading 0.0 — signal 3235 steps by 0.1, about 47 mL of a 47.5 L tank, so a
    motionless gauge is compatible with a small burn;
  · a counter that moved by less than the noise floor but did move;
  · no tank reading at all.
"""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import db as D
import db_reader

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROW = ROOT / "web" / "templates" / "partials" / "trip_row.html"
DETAIL = ROOT / "web" / "templates" / "trip_detail.html"

KEY = "trip_fuel_all_electric"
VIN = "LVIN0000000000001"
START = datetime(2026, 7, 30, 16, 4, tzinfo=timezone.utc)
KM = 48.0
FULL = 32.825


# ── the reading itself ────────────────────────────────────────────────────────

def test_the_counter_reading_the_same_at_both_ends_is_a_measured_zero():
    """Millilitre resolution, identical at both ends, over 48 km: the generator did not run."""
    out = db_reader._reev_trip_fuel(69.1, 69.1, KM, fuel_start_l=FULL, fuel_end_l=FULL)
    assert out["fuel_used_l"] == 0, "a measured zero is reported as 'unknown'"
    assert out["fuel_source"] == "mate", "the zero does not say which reading it came from"
    assert out["engine_ran"] is False
    assert out["fuel_l_100km"] is None, "0 L/100 km is arithmetic on a zero, not a rate to print"


def test_the_cloud_saying_zero_litres_is_a_measured_zero_too():
    out = db_reader._reev_trip_fuel(None, None, KM, cloud_l=0.0)
    assert out["fuel_used_l"] == 0 and out["fuel_source"] == "cloud"
    assert out["engine_ran"] is False


def test_a_motionless_percentage_gauge_is_not_a_measured_zero():
    """Signal 3235 steps by 0.1 % — about 47 mL — so 'it did not move' is not 'nothing burned'."""
    out = db_reader._reev_trip_fuel(69.1, 69.1, KM)
    assert out["fuel_used_l"] is None, "the gauge's own resolution is being reported as a zero"
    assert out["fuel_source"] is None


def test_a_counter_that_moved_a_little_is_not_a_measured_zero():
    """Three millilitres is under the noise floor, so it is not a figure — but it is not zero
    either, and calling it zero would publish a burn as an electric drive."""
    out = db_reader._reev_trip_fuel(69.1, 69.1, KM, fuel_start_l=FULL, fuel_end_l=FULL - 0.003)
    assert out["fuel_used_l"] is None and out["fuel_source"] is None


def test_no_tank_reading_at_all_stays_unknown():
    out = db_reader._reev_trip_fuel(None, None, KM)
    assert out["fuel_used_l"] is None and out["fuel_source"] is None


def test_a_drive_that_burned_petrol_is_untouched():
    out = db_reader._reev_trip_fuel(69.1, 60.9, 77.0, fuel_start_l=FULL, fuel_end_l=28.939)
    assert out["fuel_used_l"] == 3.886 and out["engine_ran"] is True


# ── what reaches the screen ───────────────────────────────────────────────────

def _install(tmp_path, monkeypatch, *, end_l, end_pct=69.1, start_pct=69.1, start_l=FULL):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (VIN,))
    for key, value in (("is_reev", "1"), ("timezone", "UTC"), ("setup_complete", "1"),
                       ("language", "en")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    end = START + timedelta(minutes=60)
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
              " start_soc, end_soc, ec_kwh, ec_stable, fuel_start_pct, fuel_end_pct, fuel_start_l,"
              " fuel_end_l) VALUES (1,1,?,?,?,60,80,62,9.0,1,?,?,?,?)",
              (START.isoformat(), end.isoformat(), KM, start_pct, end_pct, start_l, end_l))
    for k in range(60):
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude,"
                  " speed_kmh, soc) VALUES (1,?,?,9.0,60,?)",
                  ((START + timedelta(minutes=k)).isoformat(), 45.0 + k * 0.01, 80 - k * 0.3))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _pages(tmp_path, monkeypatch, **kw):
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    pytest.importorskip("fastapi")
    _install(tmp_path, monkeypatch, **kw)
    import main
    from starlette.testclient import TestClient
    client = TestClient(main.app)
    day = client.get("/api/trips/calendar/day",
                     params={"year": START.year, "month": START.month, "day": START.day}).text
    assert "48" in day, "the day drawer carried no trip at all — the fixture, not the feature"
    return day, client.get("/trips/1").text


def test_the_list_and_the_detail_both_print_the_zero(tmp_path, monkeypatch):
    listed, detail = _pages(tmp_path, monkeypatch, end_l=FULL)
    for where, html in (("the trips list", listed), ("the trip detail", detail)):
        assert "all electric" in html, f"{where} shows nothing where the car measured zero litres"
    # The words without the figure would read as a caption with nothing to caption. The two surfaces
    # write the zero differently — the row inline after the pump, the detail as a big figure with
    # its unit in a span — so each is asserted as it is actually rendered.
    assert "⛽ 0 L" in listed, "the trips list prints the words without the litres"
    assert ">0<span" in detail, "the trip detail prints the words without the litres"
    assert "0 L/100" not in listed and "0 L/100" not in detail, \
        "a rate computed on a zero is on screen, and it reads as a measured efficiency"


def test_an_unknown_drive_still_shows_nothing_in_the_list(tmp_path, monkeypatch):
    """The blank has to keep meaning 'we do not know' — otherwise the fix swaps one silence for a
    wrong claim, which is worse than the silence."""
    listed, detail = _pages(tmp_path, monkeypatch, end_l=None, start_l=None)
    assert "all electric" not in listed, "a drive with no tank reading is published as all-electric"
    assert "all electric" not in detail, "a drive with no tank reading is published as all-electric"
    assert "—" in detail, "the detail lost the dash that says the tank was never read"


def test_a_drive_that_burned_petrol_shows_the_litres_not_the_zero(tmp_path, monkeypatch):
    listed, detail = _pages(tmp_path, monkeypatch, end_l=28.939, end_pct=60.9)
    for where, html in (("the trips list", listed), ("the trip detail", detail)):
        assert "all electric" not in html, f"{where} calls a drive that burned 3.886 L electric"


def test_the_words_exist_in_every_language():
    locales = ROOT / "web" / "locales"
    for path in sorted(locales.glob("*.json")):
        strings = json.loads(path.read_text())["translations"]
        assert strings.get(KEY), f"{path.name} has no {KEY}"
        assert not strings[KEY].startswith("trip_fuel"), f"{path.name}: {KEY} is a stub"
        assert len(strings[KEY]) <= 20, (
            f"{path.name}: {KEY} is {len(strings[KEY])} characters — the trips column is 84px wide, "
            "the same constraint reev_elec_battery_only is held to")
