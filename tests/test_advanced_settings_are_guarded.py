"""The nine settings that silently change how Mate behaves: confirmed, recorded, and reported.

#230, 06/08/26. @adoewa's charge went unrecorded because `charge_detect_min_a` sat at **14.5 A**
where the default is 2.0 — above the 11-12 A a home AC charge moves the pack at, so `_is_charging`
returned False on all 202 polls. He says he had set 2 A, and that the poll cadence had moved too.

Silvio: *«Quei parametri non si modificano da soli e né li abbiamo toccati in qualche release»* —
both true. What the code did allow is this, verified rather than supposed:

    <form hx-post="api/settings/charge-detect" hx-trigger="change">
      <input type="range" name="charge_detect_min_a" min="0.5" max="16" step="0.5">

A **range slider** inside a form that saves on `change`. A range fires `change` the moment the thumb
is released, so a stray touch or a drag while scrolling a phone writes the new value immediately,
with no confirmation and no trace. Nine sliders across five forms behave this way — including
`poll_parked`/`poll_driving`, the other thing he reported as moved.

Three guards, and none of them is a fix for one user:

  · **Explicit save.** The slider moves freely; nothing is written until the button is pressed. A
    confirmation dialog was the alternative and was rejected: it would fire on every drag-release of
    someone deliberately adjusting. The retention card on the same page already works this way.
  · **A record.** Every change to one of these keys is written down — when, from what, to what — so
    "it changed by itself" stops being unanswerable.
  · **In the bundle.** The values with their defaults, the ones that differ marked, and the recent
    changes. Reading this file first would have replaced half a day of log archaeology.
"""
import pathlib
import re

import db as PollerDB
import db_reader
import diagnostics
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETTINGS_HTML = (ROOT / "web" / "templates" / "settings.html").read_text()

# Every slider that changes behaviour, and the form that posts it.
GUARDED_FORMS = ("api/poll-settings", "api/settings/charge-detect", "api/settings/advanced",
                 "api/settings/charger-locator/map-threshold")


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = PollerDB.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)
    return pdb


# ── 1 · nothing is written until you say so ───────────────────────────────────

def test_no_behaviour_slider_saves_on_change():
    """🔴 The mechanism itself. A `range` fires `change` on thumb-release, so `hx-trigger="change"`
    on a form containing one means an accidental drag is an accidental save."""
    forms = re.split(r"(<form[^>]*>)", SETTINGS_HTML)
    for i in range(1, len(forms), 2):
        tag, body = forms[i], forms[i + 1].split("</form>")[0]
        post = re.search(r'hx-post="([^"]+)"', tag)
        if not post or post.group(1) not in GUARDED_FORMS:
            continue
        if '<input type="range"' not in body:
            continue
        assert 'hx-trigger="change"' not in tag, \
            f"{post.group(1)} still saves a slider the moment it is released"


def test_every_guarded_form_has_a_save_button():
    """Removing the auto-save without adding a button would leave the settings unreachable."""
    forms = re.split(r"(<form[^>]*>)", SETTINGS_HTML)
    seen = set()
    for i in range(1, len(forms), 2):
        tag, body = forms[i], forms[i + 1].split("</form>")[0]
        post = re.search(r'hx-post="([^"]+)"', tag)
        if not post or post.group(1) not in GUARDED_FORMS:
            continue
        assert 'type="submit"' in body, f"{post.group(1)} has no save button"
        seen.add(post.group(1))
    assert seen == set(GUARDED_FORMS), f"a guarded form vanished: {set(GUARDED_FORMS) - seen}"


# ── 2 · and the change is written down ────────────────────────────────────────

def test_a_change_to_a_guarded_setting_is_recorded(car):
    db_reader.set_setting("charge_detect_min_a", "2.0")
    db_reader.set_setting("charge_detect_min_a", "14.5")
    rows = db_reader.get_settings_audit()
    assert any(r["key"] == "charge_detect_min_a" and r["old_value"] == "2.0"
               and r["new_value"] == "14.5" for r in rows), rows


def test_writing_the_same_value_is_not_a_change(car):
    """Saving a form re-writes every field in it. Only real movement is worth recording — otherwise
    the trail fills with noise and the one line that matters is lost in it."""
    db_reader.set_setting("charge_detect_min_a", "2.0")
    before = len(db_reader.get_settings_audit())
    db_reader.set_setting("charge_detect_min_a", "2.0")
    assert len(db_reader.get_settings_audit()) == before


def test_ordinary_settings_are_not_recorded(car):
    """The trail covers what silently changes BEHAVIOUR, not the language or the currency. A record
    of everything is a record nobody reads."""
    db_reader.set_setting("language", "nl")
    db_reader.set_setting("currency", "USD")
    assert db_reader.get_settings_audit() == []


def test_a_secret_never_reaches_the_trail(car):
    """🔴 Values are stored verbatim. A password or a token must never be one of them."""
    for key in ("mqtt_pass", "ha_token", "leapmotor_password", "abrp_token"):
        db_reader.set_setting(key, "s3cr3t-value")
    trail = db_reader.get_settings_audit()
    assert not any("s3cr3t" in (r["new_value"] or "") for r in trail), trail


# ── 3 · and it is all in the bundle ───────────────────────────────────────────

def test_the_bundle_lists_the_values_and_their_defaults(car):
    db_reader.set_setting("charge_detect_min_a", "14.5")
    body = diagnostics.build_bundle("9.9.9")
    assert "charge_detect_min_a" in body
    assert "14.5" in body and "2.0" in body


def test_a_value_off_its_default_is_marked(car):
    """The point of the section: not "here are nine numbers" but "these two are not stock"."""
    db_reader.set_setting("charge_detect_min_a", "14.5")
    section = diagnostics._advanced_settings_section()
    changed = [ln for ln in section.splitlines() if "charge_detect_min_a" in ln]
    assert changed and "⚠" in changed[0], changed


def test_a_stock_install_says_so(car):
    """Nine untouched values should read as one calm line, not as nine that need checking."""
    section = diagnostics._advanced_settings_section()
    assert "⚠" not in section, section


def test_the_bundle_shows_when_it_changed(car):
    db_reader.set_setting("poll_driving", "10")
    db_reader.set_setting("poll_driving", "60")
    section = diagnostics._advanced_settings_section()
    assert "poll_driving" in section
    assert "10" in section and "60" in section


def test_every_default_matches_the_settings_page():
    """🔴 A default written twice drifts, and then this section calls a STOCK install modified —
    the exact question it exists to answer. Checked against the settings page, which is where the
    user sees the same number, for ALL of them and not just the one I happened to think of.

    Caught two on the first run: `soh_temp_min_c` was written 10 against a real 15, and
    `map_station_min_sessions` 2 against a real 1. Both would have marked an untouched install."""
    dia = (ROOT / "web" / "diagnostics.py").read_text()
    block = dia.split("_ADVANCED_DEFAULTS = {", 1)[1].split("\n}", 1)[0]
    mine = dict(re.findall(r'"([a-z_]+)":\s*"([^"]*)"', block))
    page = dict(re.findall(r"settings\.get\('([a-z_]+)',\s*'([^']*)'\)", SETTINGS_HTML))
    assert mine, block
    for key, default in mine.items():
        if key in page:
            assert page[key] == default, \
                f"{key}: the bundle says default {default!r}, the settings page says {page[key]!r}"


def test_the_two_floors_still_match_the_poller():
    """The two that no template can vouch for, because the poller holds them in code."""
    dia = (ROOT / "web" / "diagnostics.py").read_text()
    block = dia.split("_ADVANCED_DEFAULTS = {", 1)[1].split("\n}", 1)[0]
    assert re.search(r'"charge_detect_min_a":\s*"2\.0"', block), block
    assert "_CHARGE_CURRENT_MIN_A = 2.0" in (ROOT / "poller" / "client.py").read_text()
    assert "_reconstruct_min_pct: float = 2.0" in (ROOT / "poller" / "recorder.py").read_text()
