"""DRIVE TIME must not count the hours a car was unreachable.

A reconstructed trip is an odometer jump found after the fact: Mate was offline, or the car was,
for the whole drive. Its kilometres and its energy are real — the odometer moved, and that is why
those trips exist at all — but its `duration_min` is the length of the SILENCE it was found across,
not of any driving. A ten-minute errand discovered after a night of no contact carries nine hours.

Same defect as the average charge duration, on the other table: two quantities under one word. Here
the split is finer, because the row is only half unusable — kilometres and energy stay in every
total, and only the time comes out.

Silvio has had this pointed out more than once and it was never decided; decided on 14/08/2026
with "procedi con TUTTE le modifiche in coda". → [[offline-km-are-given-to-the-next-trip]]
"""
import pytest


def _install(tmp_path, monkeypatch, trips):
    """trips: (distance_km, duration_min, reconstructed)."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    for i, (km, dur, recon) in enumerate(trips, start=1):
        c.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
            " efficiency_kwh_100km, reconstructed)"
            " VALUES (?,1,?,?,?,?,16.0,?)",
            (i, f"2026-08-{i:02d}T08:00:00+00:00", f"2026-08-{i:02d}T09:00:00+00:00",
             km, dur, recon))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def test_the_silence_is_not_driving_time(tmp_path, monkeypatch):
    """Two real half-hours and one 12 km errand found across a nine-hour blackout."""
    d = _install(tmp_path, monkeypatch, [(20, 30, 0), (25, 30, 0), (12, 540, 1)])
    assert d.get_stats_summary()["total_drive_min"] == 60


def test_the_kilometres_of_that_trip_still_count(tmp_path, monkeypatch):
    """It is only the clock that is unusable: the odometer moved, and those kilometres are the
    reason the trip was reconstructed in the first place."""
    d = _install(tmp_path, monkeypatch, [(20, 30, 0), (25, 30, 0), (12, 540, 1)])
    assert d.get_stats_summary()["total_km"] == 57


def test_the_page_can_say_how_many_it_left_out(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, [(20, 30, 0), (12, 540, 1), (8, 300, 1)])
    assert d.get_stats_summary()["drive_time_excluded"] == 2


def test_nothing_to_declare_when_mate_watched_every_drive(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, [(20, 30, 0), (25, 30, 0)])
    s = d.get_stats_summary()
    assert s["drive_time_excluded"] == 0 and s["total_drive_min"] == 60


def test_the_monthly_report_leaves_it_out_too(tmp_path, monkeypatch):
    """The same figure is printed per month on the PDF report, from a different query — fixing one
    surface and not the other is how two pages come to disagree about the same car."""
    d = _install(tmp_path, monkeypatch, [(20, 30, 0), (12, 540, 1)])
    buckets = d._collect_monthly_buckets()
    assert [b["drive_min"] for b in buckets.values()] == [30]      # both trips, one month
    assert [b["trip_count"] for b in buckets.values()] == [2], "the trip itself still counts"


# ── the page ──────────────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def test_the_statistics_page_declares_the_drives_it_left_out(tmp_path, monkeypatch):
    import asyncio
    import json
    import pathlib

    import db_reader
    _install(tmp_path, monkeypatch, [(20, 30, 0), (12, 540, 1)])
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    body = asyncio.run(main.statistics(_Req())).body.decode()
    tr = json.loads((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales" /
                     "en.json").read_text())["translations"]
    assert tr["drive_time_partial"].format(n=1) in body
