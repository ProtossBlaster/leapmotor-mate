"""The SoH reference: one default, reported in diagnostics, and correctable (#221 @danielvilhena).

Battery health divides a measured capacity by `battery_capacity_nominal_kwh` — the as-new spec,
snapshotted the first time the capacity is saved so that adopting an already-aged measurement can
never reset health to ~100 % and hide the ageing. Three things were wrong around it:

1. The same setting had TWO defaults — 67.1 in the settings form, 65.0 in get_battery_capacity_kwh.
   Whichever path wrote first decided the reference, and the number on screen was not the number
   the energy was computed with.
2. The reference was not in the diagnostics, so a bundle could not answer "why is my health above
   100 %" — the figure that decides it was simply absent from the file.
3. It was snapshotted once and frozen. A reference caught from the wrong number stayed wrong for
   ever, with no way to see or correct it.
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN = (ROOT / "web" / "main.py").read_text()
SETTINGS_HTML = (ROOT / "web" / "templates" / "settings.html").read_text()
DIAG = (ROOT / "web" / "diagnostics.py").read_text()
DB_READER = (ROOT / "web" / "db_reader.py").read_text()


# ── 1. one default ───────────────────────────────────────────────────────────

def test_the_settings_form_does_not_carry_its_own_capacity_default():
    """It rendered `settings.get('battery_capacity_kwh', '67.1')` while the code read 65.0."""
    assert "'battery_capacity_kwh', '67.1'" not in SETTINGS_HTML
    assert 'value="{{ capacity_kwh }}"' in SETTINGS_HTML


def test_the_form_is_fed_the_capacity_the_app_actually_uses():
    assert "capacity_kwh=db_reader.get_battery_capacity_kwh()" in MAIN


def test_there_is_still_exactly_one_default_in_the_code():
    """If a second literal reappears anywhere, the two can drift apart again."""
    assert DB_READER.count('get_setting("battery_capacity_kwh", "65.0")') == 1


# ── 2. reported in the diagnostics ───────────────────────────────────────────

def test_the_diagnostics_carries_the_soh_reference():
    assert '"battery_nominal_kwh": settings.get("battery_capacity_nominal_kwh"' in DIAG
    assert "SoH reference:" in DIAG, "it must be printed, not merely collected"


def test_an_unset_reference_is_said_out_loud_rather_than_left_blank():
    """Absent means "never snapshotted", which is a different answer from "we did not look"."""
    assert "not set" in DIAG.split('"battery_nominal_kwh"', 1)[1][:200]


# ── 3. correctable ───────────────────────────────────────────────────────────

def _route_body() -> str:
    return MAIN.split("async def capacity_settings(", 1)[1].split("\n@app.", 1)[0]


def test_a_typed_reference_is_honoured():
    body = _route_body()
    assert 'form.get("battery_nominal_kwh"' in body
    assert 'set_setting("battery_capacity_nominal_kwh", str(nominal))' in body


def test_leaving_it_empty_keeps_the_old_snapshot_behaviour():
    """The field is pre-filled, so most saves resend the same value — but an empty one must not
    wipe the reference, or a browser that omits the field would silently reset battery health."""
    body = _route_body()
    assert "elif not db_reader.get_setting(\"battery_capacity_nominal_kwh\", \"\")" in body


def test_a_typed_reference_is_clamped_like_the_capacity():
    """Same 10–200 kWh bounds as the field beside it: a stray keystroke must not become a
    denominator, and a zero would divide battery health by nothing."""
    body = _route_body()
    assert "max(10.0, min(float(_typed), 200.0))" in body


def test_the_field_is_prefilled_with_what_is_in_force():
    """Falls back to the capacity when never snapshotted — the same value the health page uses in
    that case, so the form never suggests a change that is not one."""
    assert 'value="{{ capacity_nominal or capacity_kwh }}"' in SETTINGS_HTML


# ── the strings ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["en", "it", "fr", "de", "nl", "pl", "pt-PT"])
def test_the_new_labels_exist_in_every_language(lang):
    d = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]
    for key in ("capacity_nominal", "capacity_nominal_desc"):
        assert d.get(key), f"{lang} is missing {key}"
        assert d[key] != json.loads((ROOT / "web" / "locales" / "en.json").read_text())[
            "translations"][key] or lang == "en", f"{lang}/{key} was left in English"
