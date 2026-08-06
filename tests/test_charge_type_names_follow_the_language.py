"""The charge type reads in your own language — Home, FREE and Manual are words, not acronyms.

@konrad300, #210, 01/08/26: on a Polish interface the charge type shows **"Home"** on the Costs
page, the charge badge, the type dropdown and the search filter, while the monthly report calls the
same thing **"Dom"**. The app contradicted itself, and the comment in the code was the part that was
wrong:

    # Labels are intentionally language-neutral (international loanwords + universal
    # electrical acronyms) so they never need translating across UI languages.

**AC, DC and HPC are acronyms and stay.** *Home*, *FREE* and *Manual* are ordinary English words.

We answered *"PR very welcome, please go ahead"* and left him the map of the keys to reuse. Six days
later it had not come, and Silvio's call on 06/08 was to do it ourselves and close.

🔑 **Two of the three words already existed**, translated by native speakers, and are reused rather
than duplicated: `report_home` (the monthly report's own "Dom"/"Casa" — the very word that exposed
the contradiction) and `charge_free`. Only *Manual* needed adding.

⚠️ **Three copies**, in three languages, and a fix that misses one is the defect returning on a
different page: the dict in `db_reader`, a Jinja tuple in `costs.html`, and a JavaScript object in
the same file. Translated at the source so the nine places that inject `charge_types` into a
template need no change and cannot drift.
"""
import json
import pathlib
import re

import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COSTS = (ROOT / "web" / "templates" / "costs.html").read_text()
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


def _t(lang):
    import i18n
    return i18n.get_t(lang)


# ── the three words follow the language ───────────────────────────────────────

@pytest.mark.parametrize("lang,home", [("it", "Casa"), ("pl", "Dom"), ("de", "Zuhause")])
def test_home_reads_in_the_users_language(lang, home, monkeypatch):
    monkeypatch.setattr(db_reader, "get_language", lambda: lang)
    assert db_reader.charge_types_localised()["HOME"]["label"] == home


def test_free_and_manual_follow_too(monkeypatch):
    monkeypatch.setattr(db_reader, "get_language", lambda: "it")
    types = db_reader.charge_types_localised()
    assert types["FREE"]["label"] == "Gratis"
    assert types["MANUAL"]["label"] not in ("Manual", "", None)


def test_the_acronyms_are_left_alone(monkeypatch):
    """AC, DC and HPC mean the same in every language Mate speaks. Translating them would be the
    opposite mistake."""
    monkeypatch.setattr(db_reader, "get_language", lambda: "pl")
    types = db_reader.charge_types_localised()
    assert types["AC"]["label"] == "AC"
    assert types["FAST"]["label"] == "DC"
    assert types["HPC"]["label"] == "HPC"


def test_english_still_says_home(monkeypatch):
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    assert db_reader.charge_types_localised()["HOME"]["label"] == "Home"


def test_the_icons_and_colours_are_untouched(monkeypatch):
    monkeypatch.setattr(db_reader, "get_language", lambda: "fr")
    for key, meta in db_reader.charge_types_localised().items():
        assert meta["icon"] == db_reader.CHARGE_TYPES[key]["icon"]
        assert meta["color"] == db_reader.CHARGE_TYPES[key]["color"]


def test_it_never_returns_the_shared_dict(monkeypatch):
    """🔴 A module-level dict handed out and then written into is a global mutated per request:
    the first Polish visitor would leave "Dom" behind for everyone."""
    monkeypatch.setattr(db_reader, "get_language", lambda: "pl")
    db_reader.charge_types_localised()
    assert db_reader.CHARGE_TYPES["HOME"]["label"] == "Home", "the source dict was mutated"


# ── every copy, not just the one that was reported ────────────────────────────

def test_the_costs_page_has_no_english_word_left():
    """Two more copies live here — a Jinja tuple and a JavaScript object — and konrad300 named the
    Costs page first. Fixing only the Python would have left the page he reported unchanged."""
    for m in re.finditer(r"""['"]Home['"]""", COSTS):
        line = COSTS[:m.start()].count("\n") + 1
        assert False, f"costs.html:{line} still hardcodes the word Home"


def test_the_costs_page_asks_for_the_translation():
    assert COSTS.count("report_home") >= 2, \
        "the Jinja row and the JavaScript object must both read the translated word"


def test_nothing_injects_the_raw_dict_into_a_template():
    """The nine routes that hand `charge_types` to a template must hand the localised one, or the
    page they render goes back to English."""
    main = (ROOT / "web" / "main.py").read_text()
    assert "db_reader.CHARGE_TYPES" not in main, \
        "a route still injects the untranslated dict"


# ── and the word that had to be added exists everywhere ───────────────────────

def test_the_manual_label_exists_in_all_seven_languages():
    for path in LOCALES:
        flat = {k: v for s in json.loads(path.read_text()).values()
                if isinstance(s, dict) for k, v in s.items()}
        assert "charge_manual" in flat, f"{path.name} is missing charge_manual"
        assert flat["charge_manual"].strip(), f"{path.name}: charge_manual is empty"
