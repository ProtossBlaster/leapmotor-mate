"""A C10 RWD still on 69.9 kWh has to be TOLD, not migrated behind its owner's back.

v3.11.2 corrected the C10 RWD default from 69.9 to 67.0 usable after @ghuaywen-ai's own charges
settled it (#246): at 69.9 the battery took 100.8% of what his charger delivered — more energy in
than out. But the correction reached NEW setups only. Every C10 already installed kept 69.9, and
its kWh, €/kWh and consumption figures have been ~4% high ever since, with nothing on any screen
saying so. Open since v3.11.2, listed as "decided by Silvio", decided on 14/08/2026.

⛔ Not a silent migration. A capacity is a value the owner is allowed to calibrate — some have,
against their own meter — and overwriting it would be exactly the kind of quiet change that makes a
number impossible to trust afterwards. So: a line in Settings, next to the field, with a button
that fills the corrected figure in. The same shape the SoH estimator's "use measured" already uses.

The notice appears ONLY on the value Mate itself wrote: a C10 sitting on exactly 69.9. Somebody who
typed 68.4 chose it, and gets nothing.
"""
import pytest


def _install(tmp_path, monkeypatch, *, car_type, capacity):
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type, capacity_kwh) VALUES"
                      " (1,'LFZTEST0000000001',?,?)", (car_type, capacity))
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_setting("battery_capacity_kwh", str(capacity))
    return db_reader


def test_a_c10_left_on_the_old_default_is_offered_the_corrected_pack(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, car_type="C10", capacity=69.9)
    assert d.superseded_pack_kwh() == 67.0


def test_a_c10_already_corrected_is_left_alone(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, car_type="C10", capacity=67.0)
    assert d.superseded_pack_kwh() is None


def test_a_capacity_the_owner_calibrated_is_never_second_guessed(tmp_path, monkeypatch):
    """68.4 is nobody's default: it was typed, probably against a meter."""
    d = _install(tmp_path, monkeypatch, car_type="C10", capacity=68.4)
    assert d.superseded_pack_kwh() is None


def test_the_awd_is_not_touched(tmp_path, monkeypatch):
    """81.9 carries the same suspicion and NO data to settle it — a real charge with the meter
    typed in, from an AWD owner, is what it waits for. Guessing by resemblance is how 69.9 got
    here in the first place."""
    d = _install(tmp_path, monkeypatch, car_type="C10", capacity=81.9)
    assert d.superseded_pack_kwh() is None


def test_another_model_on_69_9_is_not_a_c10(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, car_type="B10", capacity=69.9)
    assert d.superseded_pack_kwh() is None


# ── the page ──────────────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _settings(tmp_path, monkeypatch, *, car_type, capacity):
    import asyncio

    import db_reader
    _install(tmp_path, monkeypatch, car_type=car_type, capacity=capacity)
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    return asyncio.run(main.settings_page(_Req())).body.decode()


def test_settings_says_it_and_offers_the_button(tmp_path, monkeypatch):
    import json
    import pathlib
    body = _settings(tmp_path, monkeypatch, car_type="C10", capacity=69.9)
    tr = json.loads((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales" /
                     "en.json").read_text())["translations"]
    assert tr["capacity_superseded"].format(kwh="67.0") in body
    assert tr["capacity_use_corrected"].format(kwh="67.0") in body
    assert "{kwh}" not in body, "a placeholder reached the page unrendered"


def test_the_figure_wears_the_language_s_own_decimal_mark(tmp_path, monkeypatch):
    """English writes 67.0 and Italian 67,0 — every other number on that page already does, and a
    stray dot beside a field reading 69,9 is the reader wondering which one Mate believes."""
    import db_reader
    _install(tmp_path, monkeypatch, car_type="C10", capacity=69.9)
    db_reader.set_setting("language", "it")
    import asyncio

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    body = asyncio.run(main.settings_page(_Req())).body.decode()
    assert "Usa 67,0" in body, "the button writes a dot next to a field reading 69,9"
    assert "67,0 kWh utili" in body
    # …and the value the button TYPES into the field keeps the dot: it goes into an <input
    # type=number>, which a comma would make invalid. The separator is a reading convention, not
    # a storage one, and mixing the two up is how a correction button silently stops working.
    assert "battery_capacity_kwh.value='67.0'" in body


def test_settings_stays_quiet_for_everyone_else(tmp_path, monkeypatch):
    import json
    import pathlib
    body = _settings(tmp_path, monkeypatch, car_type="C10", capacity=67.0)
    tr = json.loads((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales" /
                     "en.json").read_text())["translations"]
    assert tr["capacity_superseded"].format(kwh="67.0") not in body
