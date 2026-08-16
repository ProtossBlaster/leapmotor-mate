"""The charge ETA must not take its target from a charging plan that is switched OFF (#252).

@ghuaywen-ai, C10, both screens captured at the same minute:

  * the official app — **Charging upper limit 100%**, slider hard right, "Optimum: 90%" underneath,
    and the **Charging plan toggle OFF**;
  * Mate — `1.8 kW · 39% · 23h 25m to 90%`.

The remaining time is identical in both, so that field is read correctly. The target is not.

What Mate calls "the charge limit" is `config["3"]["percent"]` of the car's status, and the cloud
library names that block for what it is: `percent` → `chargesocSetting`, `isEnable` →
`chargeScheduleEnabled`, `beginTime`/`endTime`/`cycles` → the schedule. It is the **charging
PLAN's** target SoC, sitting next to the plan's own on/off switch — NOT the upper limit the owner
drags in the app, which the cloud does not expose to us at all.

So Mate was labelling one setting with the other's name, and doing it even when the plan that owns
that number was switched off. `_charge_window()`, two functions away, has always refused to print a
window from a disabled plan for exactly this reason; the target simply never got the same rule.

With the plan off there is no target we can know, and the ETA falls back to 100% — which is what
his car was actually charging to. → [[feedback-two-numbers-one-word]]
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


def _install(tmp_path, monkeypatch, *, plan_soc, plan_enabled):
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    if plan_soc is not None:
        db_reader.set_setting("charge_limit_percent", str(plan_soc))
    db_reader.set_setting("charge_sched_enabled", "1" if plan_enabled else "0")

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    return main


def test_a_plan_that_is_off_lends_no_target(tmp_path, monkeypatch):
    """His case: plan off, its stored SoC 90, car charging to 100."""
    main = _install(tmp_path, monkeypatch, plan_soc=90, plan_enabled=False)
    assert main._configured_charge_limit() is None


def test_a_plan_that_is_on_lends_its_target(tmp_path, monkeypatch):
    """When the plan really is running, its SoC IS where the charge stops."""
    main = _install(tmp_path, monkeypatch, plan_soc=80, plan_enabled=True)
    assert main._configured_charge_limit() == 80


def test_no_stored_target_is_still_unknown(tmp_path, monkeypatch):
    main = _install(tmp_path, monkeypatch, plan_soc=None, plan_enabled=True)
    assert main._configured_charge_limit() is None


def test_the_hero_says_a_hundred_when_the_plan_is_off(tmp_path, monkeypatch):
    """The label falls back to 100% — what his car was charging to — instead of the plan's 90."""
    import asyncio

    class _Req:
        headers = {"x-ingress-path": ""}
        cookies: dict = {}
        query_params: dict = {}

    import db as D
    import db_reader
    main = _install(tmp_path, monkeypatch, plan_soc=90, plan_enabled=False)
    pdb = D.Database(db_reader.DB_PATH)
    pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
                      " odometer_km, plug_connected, remaining_charge_min)"
                      " VALUES (1,'2026-08-15T18:20:00+00:00',39,1,0,5000,1,1405)")
    pdb._conn.commit()
    pdb._conn.close()

    body = asyncio.run(main.overview(_Req())).body.decode()
    assert "to 100%" in body, "the ETA still quotes the switched-off plan"
    assert "to 90%" not in body


# ── the same number, the same name, everywhere ────────────────────────────────
def test_no_screen_calls_the_plans_target_the_charge_limit():
    """The Charges page shows and SETS this very number, and the battery bar marks it. Fixing the
    hero and leaving those two calling it "the charge limit" means the reader meets the old wrong
    word two clicks away — and there it can be changed, believing it moves the car's own limit.
    → [[feedback-gate-a-feature-find-every-copy]]"""
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for lang, wrong in (("en", "charge limit"), ("it", "limite di carica")):
        tr = json.loads((root / "web" / "locales" / f"{lang}.json").read_text())["translations"]
        assert wrong not in tr["charge_limit"].lower(), f"{lang}: {tr['charge_limit']!r}"
        # …and the description has to say WHICH of the two settings this is, since the car has both.
        assert any(w in tr["charge_limit_desc"].lower()
                   for w in ("plan", "programma", "programmazione")), tr["charge_limit_desc"]
