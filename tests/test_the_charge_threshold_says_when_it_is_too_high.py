"""A charge-detection floor set above the car's own charging current must say so (#250, @riri19).

His two sessions of 14–15 August, from his bundle: the first charge recorded, the second one opened
**93.5%** where the first had ended at 80% — 13.5 points and, by his own wallbox counter, **10.2 kWh**
that no charge accounts for.

The cause was one setting. `charge_detect_min_a` was at **13.5 A**; his car charges at home at
**12.9–13.4 A**. In two days the poller saw "charging" in exactly **seven frames**, every one of them
at −13.5/−13.6/−13.7 — the rare jitter that touches the floor. Everything in between read as parked,
including the 95 minutes from 03:50 to 05:25 in which the battery climbed from 79.9% to 93.5%.

The floor did not produce "no charges", which someone would notice: it produced **half a charge**,
which reads as Mate losing data.

Nothing measures this on its own, so the page has to: the current the car really draws is in the
positions Mate records on every poll, whatever it thinks the state is. If the floor is above the
highest charging current ever seen, that is not a preference — it is the detector switched off.

⚠️ Silent when nothing has been measured: an install that has never charged says nothing, rather
than warning about a number nobody can check. → [[signal-absent-is-not-signal-zero]]
"""
import pytest


def _install(tmp_path, monkeypatch, *, threshold, currents):
    """`currents` are amps seen while the cable was in — negative is charging, as the car reports."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    for i, a in enumerate(currents):
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
                  " odometer_km, plug_connected, charge_current_a)"
                  " VALUES (1,?,55,0,0,1000,1,?)", (f"2026-08-15T0{i//60}:{i%60:02d}:00+00:00", a))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_setting("charge_detect_min_a", str(threshold))
    return db_reader


def test_a_floor_above_the_cars_own_current_is_reported(tmp_path, monkeypatch):
    """His case, to the tenth of an amp."""
    d = _install(tmp_path, monkeypatch, threshold=13.5, currents=[-12.9, -13.3, -13.4, -13.0])
    assert d.charge_threshold_too_high() == 13.4


def test_a_floor_under_it_says_nothing(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, threshold=2.0, currents=[-12.9, -13.4])
    assert d.charge_threshold_too_high() is None


def test_the_boundary_is_not_a_warning(tmp_path, monkeypatch):
    """Exactly at the peak the detector still fires — that is the seven frames he did get."""
    d = _install(tmp_path, monkeypatch, threshold=13.4, currents=[-12.9, -13.4])
    assert d.charge_threshold_too_high() is None


def test_an_install_that_has_never_charged_is_left_alone(tmp_path, monkeypatch):
    """No measurement is not a measurement of zero: warning here would accuse a number nobody has."""
    d = _install(tmp_path, monkeypatch, threshold=13.5, currents=[])
    assert d.charge_threshold_too_high() is None


def test_resting_current_is_not_a_charge(tmp_path, monkeypatch):
    """Plugged in and idle reads ±0.1–0.5 A. Taking that as "the car charges at 0.5 A" would warn
    every install on the default 2 A floor."""
    d = _install(tmp_path, monkeypatch, threshold=2.0, currents=[0.1, -0.4, 0.5, -0.1])
    assert d.charge_threshold_too_high() is None


def test_the_sign_does_not_matter_for_the_peak(tmp_path, monkeypatch):
    """The car reports charging as negative; the setting is a magnitude."""
    d = _install(tmp_path, monkeypatch, threshold=20.0, currents=[-15.2, 3.0])
    assert d.charge_threshold_too_high() == 15.2


# ── the page ──────────────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _settings_page(tmp_path, monkeypatch, **kw):
    import asyncio
    db_reader = _install(tmp_path, monkeypatch, **kw)
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    return asyncio.run(main.settings_page(_Req())).body.decode()


def test_settings_says_it_beside_the_slider(tmp_path, monkeypatch):
    import json
    import pathlib
    body = _settings_page(tmp_path, monkeypatch, threshold=13.5,
                          currents=[-12.9, -13.3, -13.4, -13.0])
    tr = json.loads((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales" /
                     "en.json").read_text())["translations"]
    assert tr["charge_detect_too_high"].format(a="13.4") in body
    assert "{a}" not in body, "a placeholder reached the page unrendered"


def test_settings_stays_quiet_when_the_floor_is_sane(tmp_path, monkeypatch):
    import json
    import pathlib
    body = _settings_page(tmp_path, monkeypatch, threshold=2.0, currents=[-12.9, -13.4])
    tr = json.loads((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales" /
                     "en.json").read_text())["translations"]
    assert tr["charge_detect_too_high"].format(a="13.4") not in body
