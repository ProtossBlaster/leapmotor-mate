"""Auto-classify new charges as HOME (main discussion #255, asked by @CartusGress).

An install with no wallbox and no Home Assistant has no signal that says "this charge was at home":
every charge is born unclassified (`location_type` NULL) and the owner tags each one by hand. For
someone who only ever charges at home — several short top-ups a day — that is a lot of identical
clicks. This opt-in turns it around: while it is on, a new charge is born HOME, editable afterwards
for the rare public one. It is guarded by an explicit confirm so it can never be flipped by accident
(Silvio's requirement) and it works only FORWARD — the past backlog is left exactly as it was.
"""
import json
import pathlib
import re
import types

import db as D          # the POLLER db (create_charge lives here) — same import the other poller tests use
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETTINGS_HTML = (ROOT / "web" / "templates" / "settings.html").read_text(encoding="utf-8")
_BOX = ROOT / "web" / "templates" / "partials" / "home_default_box.html"
BOX_HTML = _BOX.read_text(encoding="utf-8") if _BOX.exists() else ""
LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es")


@pytest.fixture
def db(tmp_path):
    d = D.Database(str(tmp_path / "t.db"))
    d._conn.execute("INSERT OR IGNORE INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    d._conn.commit()
    return d


def _location_type(db, charge_id):
    return db._conn.execute("SELECT location_type FROM charges WHERE id = ?", (charge_id,)).fetchone()[0]


def _start(db):
    return db.create_charge(1, types.SimpleNamespace(soc=40, latitude=None, longitude=None))


# ── the behaviour ────────────────────────────────────────────────────────────

def test_a_new_charge_is_home_when_the_default_is_on(db):
    db.set_setting("home_charges_default", "1")
    assert _location_type(db, _start(db)) == "HOME"


def test_a_new_charge_stays_unclassified_when_the_default_is_off(db):
    db.set_setting("home_charges_default", "0")
    assert _location_type(db, _start(db)) is None


def test_the_shipped_default_is_off(db):
    """No setting written at all — a fresh install must behave exactly as before: charges are born
    unclassified and wait for the owner. Turning this into an opt-OUT would retag everyone's charges."""
    assert _location_type(db, _start(db)) is None


# ── the guard: it cannot be flipped by accident ──────────────────────────────

def test_enabling_is_behind_an_explicit_confirm():
    """Silvio: 'evitare che qualcuno l'attivi accidentalmente'. The ENABLE control (posts value 1)
    carries hx-confirm — Mate's own dialog — so a stray click asks first instead of silently
    retagging every future charge. Disabling is safe and needs no confirm."""
    enable = next((tag for tag in re.findall(r"<button\b[^>]*>", BOX_HTML)
                   if '"home_charges_default":"1"' in tag), "")
    assert enable, "no enable button posting home_charges_default=1 found in the box"
    assert "hx-confirm=" in enable, "the enable button must be guarded by hx-confirm"


def test_the_section_shows_the_control():
    assert "home_default_box.html" in SETTINGS_HTML


# ── every language carries the strings ───────────────────────────────────────

@pytest.mark.parametrize("lang", LANGS)
def test_the_labels_exist_in_every_language(lang):
    d = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]
    for key in ("home_default_title", "home_default_desc", "home_default_enable",
                "home_default_disable", "home_default_on", "home_default_warn"):
        assert d.get(key), f"{lang} is missing {key}"
