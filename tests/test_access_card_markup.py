"""The Access card's markup — checked without importing the app.

These read `settings.html` and nothing else, so they must NOT sit next to the endpoint tests: those
import `web/main.py`, which pulls in FastAPI, which CI doesn't install — the whole file would be
skipped there, and a template guard that only runs on the maintainer's laptop guards nothing. Same
lesson as v3.5.1, applied before shipping this time rather than after.
"""
import pathlib
import re

SETTINGS = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" / "settings.html"


def _html():
    return SETTINGS.read_text(encoding="utf-8")


def _password_forms(html):
    return re.findall(r'<form[^>]*hx-post="api/settings/password"[^>]*>.*?</form>', html, re.S)

def test_both_forms_ask_twice_and_carry_the_mismatch_message():
    """The card has two forms — first run ("choose a password") and later ("new password"). The
    first one is the one that locks you out silently, so it must not be the one that got forgotten.
    """
    html = _html()
    forms = _password_forms(html)
    assert len(forms) == 2, "expected the set-it and change-it forms"
    for f in forms:
        assert 'name="password"' in f and 'name="password2"' in f
        assert "auth_mismatch" in f, "no message for the browser to show when they differ"
        assert f.count("lmPwMatch") == 2, "both boxes must re-check as they are typed"


def test_the_way_out_is_written_down_where_you_set_it():
    # Losing it locks you out of your own instance; the card has to say what to do about that.
    import pathlib

    html = _html()
    assert "auth_lockout_hint" in html


def test_each_box_has_its_own_reveal_button():
    """Asked for after the confirmation went in: a way to actually look at what you typed.

    One button per box rather than one for both — the pair exists so you can compare them, and
    revealing only the one you are unsure of is the smaller exposure. It re-hides itself on blur,
    so a revealed password can't be left sitting on a shared screen."""
    html = _html()
    forms = _password_forms(html)
    for f in forms:
        assert f.count("lmPwReveal") == 2, "one reveal button per box"
        assert f.count('data-eye="on"') == 2 and f.count('data-eye="off"') == 2
    assert "addEventListener('blur'" in html, "a revealed password must re-hide itself"


def test_no_template_ever_puts_markup_inside_an_attribute():
    """The first version of that button carried the two icons as `data-on="<svg …>"`.

    An SVG inside an attribute ends the attribute on its own first quote: the browser then parses
    the rest of the tag as garbage and the ENTIRE card renders as an empty box — open, 66 px tall,
    no error anywhere. Jinja's `|e` doesn't save you either, because a macro's output is already
    Markup and escaping it is a no-op. It was caught by looking at the page, which is the only
    reason it isn't in a release.
    """
    bad = []
    for p in (SETTINGS.parent).rglob("*.html"):
        txt = p.read_text(encoding="utf-8")
        for m in re.finditer(r'\s([a-zA-Z-]+)="([^"]*<[a-zA-Z][^"]*)"', txt):
            if m.group(1).startswith(("hx-", "on")):      # hx-confirm / onclick legitimately hold "<"
                continue
            bad.append(f"{p.name}: {m.group(1)}=\"{m.group(2)[:40]}…\"")
    assert not bad, "markup inside an attribute value:\n" + "\n".join(bad)
