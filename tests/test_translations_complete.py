"""Every language must carry every string, on every surface that has strings.

This exists because nothing was checking. Two separate holes had been open for a while and neither
produced an error anywhere:

  * de.json was 80 strings short and pl.json 91, so German and Polish users read parts of the app
    in English — including a beta tester who sends us data in German every day.
  * The setup wizard keeps its OWN dictionary, separate from the locale files, and `acctWarn` was
    missing from the French and German copies. That string is the warning to use an account
    dedicated to Mate — the single most important sentence in the wizard and the cause of the most
    common support case there is. The wizard reads it with `s.acctWarn` and assigns it straight to
    textContent, and JavaScript turns an undefined into the literal text "undefined", so those two
    languages showed the word "undefined" where the warning belongs.

Both were invisible: a missing key isn't a crash, it's a gap. The only thing that finds a gap is
something that counts. Hence this file.

The wizard is parsed out of the template rather than imported, because it lives in JavaScript
inside HTML. Parsing is done by finding each language's opening line and reading to its closing
brace — the blocks contain no nested objects — instead of by one big regex, which quietly matched
different blocks depending on indentation while this was being written.
"""
import json
import pathlib
import re

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
LOCALES = WEB / "locales"
LANGS = ("en", "it", "fr", "de", "pl", "pt-PT")


def _flat(d, prefix=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def _locale(lang):
    return _flat(json.loads((LOCALES / f"{lang}.json").read_text()))


def _wizard_blocks():
    """{lang: {key, ...}} for the dictionaries inside setup.html."""
    lines = (WEB / "templates" / "setup.html").read_text().split("\n")
    out = {}
    for lang in LANGS:
        opener = re.compile(rf"^\s*['\"]?{re.escape(lang)}['\"]?:\s*\{{\s*$")
        start = next((i for i, ln in enumerate(lines) if opener.match(ln)), None)
        assert start is not None, f"the setup wizard has no {lang} block at all"
        end = next(i for i in range(start + 1, len(lines)) if re.match(r"^\s*\},?\s*$", lines[i]))
        body = "\n".join(lines[start + 1:end])
        out[lang] = set(re.findall(r"^\s*(\w+):\s*[\"'`]", body, re.M))
    return out


@pytest.mark.parametrize("lang", [l for l in LANGS if l != "en"])
def test_locale_has_every_string(lang):
    en, other = _locale("en"), _locale(lang)
    missing = sorted(k for k in en if k not in other)
    assert not missing, f"{lang}.json is missing {len(missing)}: {missing[:10]}"


@pytest.mark.parametrize("lang", [l for l in LANGS if l != "en"])
def test_locale_has_no_stray_strings(lang):
    """A key that exists only in one language is a rename that half-landed."""
    en, other = _locale("en"), _locale(lang)
    stray = sorted(k for k in other if k not in en)
    assert not stray, f"{lang}.json has {len(stray)} keys English doesn't: {stray[:10]}"


def test_every_language_has_a_file():
    for lang in LANGS:
        assert (LOCALES / f"{lang}.json").is_file(), f"no locale file for {lang}"


@pytest.mark.parametrize("lang", [l for l in LANGS if l != "en"])
def test_the_wizard_has_every_string_too(lang):
    """The wizard's dictionary is a SECOND copy, and this is the one that was short."""
    blocks = _wizard_blocks()
    missing = sorted(blocks["en"] - blocks[lang])
    assert not missing, f"the setup wizard's {lang} block is missing {missing}"


def test_the_account_warning_is_in_every_wizard_language():
    """Named on its own because of what it costs when it is absent: the user signs in with the
    same account as the official app, the two evict each other's session, the car goes offline
    and the history fills with holes that nothing explains."""
    for lang, keys in _wizard_blocks().items():
        assert "acctWarn" in keys, f"the {lang} wizard would print 'undefined' instead of the warning"


def test_the_allowed_language_list_agrees_with_itself_and_with_disk():
    """main.py declares the accepted languages in more than one place. Add a language, edit one of
    them, and it works on one screen and silently resets to English on the other."""
    text = (WEB / "main.py").read_text()
    lists = re.findall(r'lang if lang in \(([^)]*)\)', text)
    assert len(lists) >= 2, "the language tuple moved — this test is now watching nothing"
    parsed = [tuple(re.findall(r'"([\w-]+)"', l)) for l in lists]
    assert len(set(parsed)) == 1, f"main.py disagrees with itself about languages: {set(parsed)}"
    assert set(parsed[0]) == set(LANGS), f"main.py accepts {parsed[0]}, files on disk are {LANGS}"
