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
import pathlib
import re

import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COSTS = (ROOT / "web" / "templates" / "costs.html").read_text()


def _t(lang):
    import i18n
    return i18n.get_t(lang)


# ── the three words follow the language ───────────────────────────────────────

@pytest.mark.parametrize("lang,home", [("it", "Casa"), ("pl", "Dom"), ("de", "Zuhause")])
def test_home_reads_in_the_users_language(lang, home, monkeypatch):
    monkeypatch.setattr(db_reader, "get_language", lambda: lang)
    assert db_reader.charge_types_localised()["HOME"]["label"] == home


def test_free_follows_too(monkeypatch):
    monkeypatch.setattr(db_reader, "get_language", lambda: "it")
    types = db_reader.charge_types_localised()
    assert types["FREE"]["label"] == "Gratis"


def test_manual_is_no_longer_a_charge_type(monkeypatch):
    """'MANUAL' used to be a seventh, pricing-basis entry wearing a location's name — it never
    belonged among real types, and it is gone now that cost_manual carries that meaning instead."""
    monkeypatch.setattr(db_reader, "get_language", lambda: "it")
    assert "MANUAL" not in db_reader.charge_types_localised()
    assert "MANUAL" not in db_reader.CHARGE_TYPES


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
