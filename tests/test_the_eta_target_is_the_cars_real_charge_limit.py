"""The charge-ETA label on the Overview hero must show the car's REAL charge limit — the SoC the
charge will actually stop at — not a hardcoded 100% (@pdifeo, beta #33).

@pdifeo, C10 REEV, charging: the app's "max charge limit" slider is at 90%, and his charges stop at
90% (and at 95/100 on the days he moves it). Mate's **Charges page** shows the right figure — it
reads config["3"]["percent"], the max-charge SoC the cloud DOES send, captured by the poll loop on
every change (poller/main.py: "works even when the limit is changed from the official app") and
stored per-VIN. The **hero**, though, read a different, gated source and fell back to 100%.

So the fix points the hero at the same real value the Charges page already uses: the poller's
per-VIN charge_limit_percent. None only when the car genuinely doesn't report it (e.g. T03 named-
field responses) → then the hero shows the remaining time with no target, never a guessed 100.

This SUPERSEDES the earlier gate (#252) that hid the value whenever the charging PLAN was switched
off — which is the common case (most owners just use the slider, not timed charging), and is
exactly why @pdifeo saw "100%". → [[reev-owner-must-use-betatester]] · [[feedback-search-the-data-not-the-name]]
"""
import re

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

_VIN = "LFZTEST0000000001"


def _install(tmp_path, monkeypatch, *, limit_pct, shared_only=False):
    """Store the limit the way the POLLER stores it: per-VIN key charge_limit_percent_<vin>
    (config["3"]["percent"] captured from the car). shared_only instead writes the non-VIN key —
    the value Mate's Set-limit button types in, which the hero must ignore."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (_VIN,))
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    if limit_pct is not None:
        key = "charge_limit_percent" if shared_only else f"charge_limit_percent_{_VIN.lower()}"
        db_reader.set_setting(key, str(limit_pct))

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    return main


def _charging_body(main):
    import asyncio

    class _Req:
        headers = {"x-ingress-path": ""}
        cookies: dict = {}
        query_params: dict = {}

    import db as D
    import db_reader
    pdb = D.Database(db_reader.DB_PATH)
    pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
                      " odometer_km, plug_connected, remaining_charge_min)"
                      " VALUES (1,'2026-08-15T18:20:00+00:00',39,1,0,5000,1,1405)")
    pdb._conn.commit()
    pdb._conn.close()
    return asyncio.run(main.overview(_Req())).body.decode()


# ── the function ──────────────────────────────────────────────────────────────
def test_the_real_limit_is_returned_regardless_of_the_plan(tmp_path, monkeypatch):
    """The car reports a 90% max-charge limit → that is the target, plan on or off."""
    main = _install(tmp_path, monkeypatch, limit_pct=90)
    assert main._configured_charge_limit(_VIN) == 90


def test_none_when_the_car_reports_no_limit(tmp_path, monkeypatch):
    main = _install(tmp_path, monkeypatch, limit_pct=None)
    assert main._configured_charge_limit(_VIN) is None


def test_a_value_typed_into_mate_is_never_used(tmp_path, monkeypatch):
    """The non-VIN key is what Mate's own Set-limit button writes — a value TYPED into Mate, not one
    the car reported. The hero must ignore it: a target is shown ONLY when the car itself declared a
    limit (the per-VIN key the poll loop fills). No car-reported value → None, even if a typed one
    sits in the shared key."""
    main = _install(tmp_path, monkeypatch, limit_pct=75, shared_only=True)
    assert main._configured_charge_limit(_VIN) is None


# ── the hero label ────────────────────────────────────────────────────────────
def test_the_hero_shows_the_real_limit_not_a_hundred(tmp_path, monkeypatch):
    """@pdifeo's case: charging, limit 90 → the hero must say "to 90%", never "to 100%"."""
    main = _install(tmp_path, monkeypatch, limit_pct=90)
    body = _charging_body(main)
    assert "to 90%" in body, "the hero must show the car's real charge limit"
    assert "to 100%" not in body, "no hardcoded 100% fallback"


def test_the_hero_shows_only_the_time_when_the_limit_is_unknown(tmp_path, monkeypatch):
    """Car doesn't report a limit (e.g. T03) → show the remaining time, invent no target."""
    main = _install(tmp_path, monkeypatch, limit_pct=None)
    body = _charging_body(main)
    assert "23h 25m" in body, "the remaining time must still be shown"
    assert re.search(r"to \d+%", body) is None, "no target percentage when the car reports none"


# ── the same number, the same name, everywhere ────────────────────────────────
def test_no_screen_calls_the_plans_target_the_charge_limit():
    """The Charges page shows and SETS this number and the battery bar marks it; its label must not
    be the bare "charge limit", and its description has to say which of the car's two settings it is.
    → [[feedback-gate-a-feature-find-every-copy]]"""
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for lang, wrong in (("en", "charge limit"), ("it", "limite di carica")):
        tr = json.loads((root / "web" / "locales" / f"{lang}.json").read_text())["translations"]
        assert wrong not in tr["charge_limit"].lower(), f"{lang}: {tr['charge_limit']!r}"
        assert any(w in tr["charge_limit_desc"].lower()
                   for w in ("plan", "programma", "programmazione")), tr["charge_limit_desc"]
