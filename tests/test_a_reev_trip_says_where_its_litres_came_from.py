"""A range-extender's litres say which of the car's two answers is on screen.

Mate now has two sources for the same drive's petrol and they disagree by about a fifth: the car's
own cloud record (`driveReevOil` — 4.9 L on the one drive whose figure the owner can also read in the
official Leapmotor app) and the tank's own arithmetic (3.886 L on that same drive, measured 145 times
at millilitre resolution). The cloud wins where a record matches, the tank answers otherwise — so on
a REEV with any history the page shows BOTH bases, twenty per cent apart, under one unit.

Unlabelled that is the defect Silvio names every time: the same word over two numbers that are each
correct. So the figure carries where it came from, in the slot the ELECTRIC figure already uses for
exactly this (`trip_energy_source_*`, since v3.x) — same key shape, same styling, same place.

🔑 The slot is free on a range-extender and that is not a coincidence: `select_energy` excludes REEV
cars on purpose (the generator refills the pack mid-drive, so the cloud's `totalEnergy` is not that
car's appetite), so `energy_source` is None there and nothing is being displaced. On a plain electric
car the energy label keeps the slot and no fuel label exists to compete for it.
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

VIN = "LVIN0000000000001"
START = datetime(2026, 9, 19, 14, 54, tzinfo=timezone.utc)
MINUTES = 80
KM = 77.0
CLOUD_L = 4.9
MATE_L = 3.886


def _install(tmp_path, monkeypatch, *, cloud=True, litres=True):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (VIN,))
    for key, value in (("is_reev", "1"), ("timezone", "UTC"), ("setup_complete", "1"),
                       ("language", "en")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    end = START + timedelta(minutes=MINUTES)
    tank = ((69.1, 60.9, 32.825, 28.939) if litres else (69.1, 69.1, 32.825, 32.825))
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
              " start_soc, end_soc, ec_kwh, ec_stable, fuel_start_pct, fuel_end_pct, fuel_start_l,"
              " fuel_end_l) VALUES (1,1,?,?,?,?,80,62,0.3,1,?,?,?,?)",
              (START.isoformat(), end.isoformat(), KM, MINUTES) + tank)
    for k in range(MINUTES):
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude,"
                  " speed_kmh, soc) VALUES (1,?,?,9.0,60,?)",
                  ((START + timedelta(minutes=k)).isoformat(), 45.0 + k * 0.01, 80 - k * 0.2))
    c.execute("CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records"
              " (id INTEGER PRIMARY KEY, kind TEXT, payload_json TEXT)")
    if cloud:
        payload = {"vin": VIN, "routeStartTs": int(START.timestamp() * 1000),
                   "routeEndTs": int(end.timestamp() * 1000), "totalEnergy": 0.3,
                   "totalMileage": KM, "maxSpeed": 120,
                   "driveReevOil": CLOUD_L if litres else 0.0}
        c.execute("INSERT INTO api_lab_cloud_history_records (kind, payload_json)"
                  " VALUES ('mileage',?)", (json.dumps(payload),))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _pages(tmp_path, monkeypatch, **kw):
    """The two SERVED surfaces, because a label can be right in the context and never reach the screen.

    The list is the day DRAWER, not `/trips`: the Viaggi page is a calendar that loads a day's rows
    lazily, so the rows — and `partials/trip_row.html` with them — arrive from
    `/api/trips/calendar/day`. Asking `/trips` renders the month totals and no row at all, which is
    how the first version of this test reported the label missing when it was simply not on that
    response."""
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    pytest.importorskip("fastapi")
    _install(tmp_path, monkeypatch, **kw)
    import main
    from starlette.testclient import TestClient
    client = TestClient(main.app)
    day = client.get("/api/trips/calendar/day",
                     params={"year": START.year, "month": START.month, "day": START.day}).text
    assert "77" in day, "the day drawer carried no trip at all — the fixture, not the label"
    return day, client.get("/trips/1").text


# ── what the reader sees ──────────────────────────────────────────────────────

def test_the_cloud_figure_says_it_is_the_cars_own_history(tmp_path, monkeypatch):
    listed, detail = _pages(tmp_path, monkeypatch)
    for where, html in (("the trips list", listed), ("the trip detail", detail)):
        assert "Leapmotor history" in html, f"{where} prints 4.9 L without saying whose figure it is"
        assert "From the tank" not in html, f"{where} names the wrong source"


def test_the_tanks_figure_says_it_came_from_the_tank(tmp_path, monkeypatch):
    listed, detail = _pages(tmp_path, monkeypatch, cloud=False)
    for where, html in (("the trips list", listed), ("the trip detail", detail)):
        assert "From the tank" in html, f"{where} prints 3.886 L without saying whose figure it is"
        assert "Leapmotor history" not in html, f"{where} names the wrong source"


def test_a_drive_with_no_petrol_carries_no_label(tmp_path, monkeypatch):
    """The cloud answers 0.0 on an electric drive and that IS an answer — but there is no figure to
    attribute, so a source beside nothing is noise."""
    listed, detail = _pages(tmp_path, monkeypatch, litres=False)
    for where, html in (("the trips list", listed), ("the trip detail", detail)):
        assert "From the tank" not in html and "Leapmotor history" not in html, \
            f"{where} names a source for a drive that burned nothing"


# ── and where it sits ─────────────────────────────────────────────────────────

def test_the_fuel_label_takes_the_slot_the_energy_label_leaves_free(tmp_path, monkeypatch):
    """Not a new decoration: the same slot, the same styling, the same key shape. On a REEV
    `energy_source` is None by design, so the fuel source has the slot to itself; on a plain electric
    car the energy label keeps it and there is no fuel figure to name."""
    row = ROW.read_text()
    assert "{% elif trip.fuel_source" in row, \
        "the fuel source does not reuse the energy label's slot — two labels will compete"
    assert row.index("trip_energy_source_") < row.index("trip_fuel_source_"), \
        "the energy label must still win the slot where it exists"


def test_the_detail_keeps_the_source_out_of_the_truncating_heading(tmp_path, monkeypatch):
    """It went beside the ⛽ heading first, mirroring the ⚡ box — and the box is about 70px wide in
    the two-column summary, so `truncate` ate it: the heading rendered "⛽ Carburan…" and the label
    never appeared at all. Found by opening the page, which is the only thing that finds it.

    So: inside the box, on a line of its own, under the figures it belongs to."""
    detail = DETAIL.read_text()
    heading = next(l for l in detail.splitlines() if "trip_area_fuel" in l)
    assert "truncate" in heading                    # the constraint that caused it
    assert "trip_fuel_source_" not in heading, \
        "the source is back in the heading, where truncate eats it"
    box = detail[detail.index("trip_area_fuel"):]
    box = box[:box.index("{% endif %}\n        </div>")] if "{% endif %}\n        </div>" in box else box[:1800]
    assert "trip_fuel_source_" in box, "the ⛽ box says nothing about the source at all"


def test_both_labels_exist_in_every_language():
    """`test_translations_complete` compares the languages against each other, so a key added to one
    goes red there. This one holds the pair itself: a label that falls back to its own key prints
    `trip_fuel_source_cloud` on screen."""
    locales = ROOT / "web" / "locales"
    for path in sorted(locales.glob("*.json")):
        strings = json.loads(path.read_text())["translations"]
        for key in ("trip_fuel_source_cloud", "trip_fuel_source_mate"):
            assert strings.get(key), f"{path.name} has no {key}"
            assert not strings[key].startswith("trip_fuel_source"), f"{path.name}: {key} is a stub"
