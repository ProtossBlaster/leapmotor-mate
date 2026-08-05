"""Changing the car's operation PIN without unlinking the account (discussion #225, @alextchao).

*"Is there a way to update the car's pin on Mate after changing it on the car?"* — there was not.
The PIN was written in exactly two places: the setup wizard saved it, and Logout cleared it. So four
digits changed on the car meant signing out of the Leapmotor account and walking the whole wizard
again. Nothing was lost doing that — history is keyed by VIN — but nothing about it is obvious, and
Logout is a frightening button to press for a typo.

Typed twice, for the same reason as the access password (#214 @rop12770): a PIN stored with a typo
does not fail here, it fails at the CAR, later, with an error that names no digit.

⚠️ What these tests also pin down is what does NOT need doing. The stored secret already beats
`LEAPMOTOR_PIN` in both readers, and the poller already compares (user, password, PIN) against the
ones it started with on every cycle — a watcher written for the account switch that happens to cover
this exactly. Writing the secret is the whole feature; anything more would be a second mechanism.
"""
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN = (ROOT / "web" / "main.py").read_text()
SETTINGS = (ROOT / "web" / "templates" / "settings.html").read_text()
POLLER = (ROOT / "poller" / "main.py").read_text()
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


def _route() -> str:
    """The route's own body, so nothing here can be satisfied by another endpoint."""
    assert '@app.post("/api/settings/pin")' in MAIN, "the route does not exist"
    return MAIN.split('@app.post("/api/settings/pin")', 1)[1].split("\n@app.", 1)[0]


def _form() -> str:
    """The PIN form's own block in the settings template."""
    assert 'hx-post="api/settings/pin"' in SETTINGS, "the form is not on the page"
    return SETTINGS.split('hx-post="api/settings/pin"', 1)[1].split("</form>", 1)[0]


# ── the route ────────────────────────────────────────────────────────────────

def test_the_pin_is_stored_where_both_readers_look():
    body = _route()
    assert 'db_reader.set_secret("leapmotor_pin", pin)' in body
    assert "set_setting" not in body, "a PIN is a secret, not a setting: it must be encrypted"


def test_a_mismatch_is_refused_by_the_server_not_only_the_browser():
    """`required` in the form is a convenience. The check that matters is this one — the browser's
    can be skipped by anything that is not a browser."""
    body = _route()
    assert 'if pin2 != pin:' in body
    assert '422' in body
    i, j = body.index("if pin2 != pin:"), body.index('set_secret("leapmotor_pin"')
    assert i < j, "the PIN must not be written before the two are compared"


def test_an_empty_pin_is_refused():
    body = _route()
    assert "if not pin:" in body
    assert body.index("if not pin:") < body.index('set_secret("leapmotor_pin"')


def test_the_web_command_session_is_dropped_so_the_next_command_re_authenticates():
    assert "command_client._session._reset()" in _route()


def test_the_poller_already_notices_and_nothing_new_was_added_for_it():
    """The watcher was written for the account switch (Logout → new setup) and compares the whole
    triple, PIN included. A second mechanism here would be a second thing to keep in step."""
    assert '(_login_now["username"], _login_now["password"], _login_now["pin"]) != _startup_login' in POLLER
    assert "os._exit(42)" in POLLER
    assert "leapmotor_pin" not in _route().replace('db_reader.set_secret("leapmotor_pin", pin)', ""), \
        "the route touches the PIN exactly once"


# ── the form ─────────────────────────────────────────────────────────────────

def test_it_sits_under_the_account_email():
    """Silvio's call: under the Email account row, where someone looking for their login details
    already is — not in a section of its own."""
    email = SETTINGS.index("t('setup_email')")
    form = SETTINGS.index('hx-post="api/settings/pin"')
    logout = SETTINGS.index('hx-post="api/account/logout"')
    assert email < form < logout, "the PIN form belongs between the account email and Logout"


def test_both_boxes_are_there_and_both_can_be_revealed():
    """The eye is the point of typing it twice: two hidden boxes you cannot read are two chances to
    make the same typo. Anchored to the input tags, not to the word 'pin' — that word is also in the
    comment above them."""
    form = _form()
    assert len(re.findall(r'<input[^>]*\sname="pin"', form)) == 1
    assert len(re.findall(r'<input[^>]*\sname="pin2"', form)) == 1
    assert form.count("lmPwReveal(this)") == 2, "each box needs its own eye"
    assert form.count("ico_eye()") == 2 and form.count("ico_eye_off()") == 2


def test_the_two_boxes_are_matched_as_you_type():
    form = _form()
    assert 'oninput="lmPwMatch(this.form.pin2)"' in form
    assert 'data-mismatch="{{ t(\'auth_mismatch\') }}"' in form


def test_the_boxes_are_not_password_managers_business():
    """`autocomplete="off"` on both: a browser offering to save four digits as a login password, or
    filling them from one, is how the wrong PIN gets in without anyone typing it."""
    form = _form()
    assert form.count('autocomplete="off"') == 2


def test_it_keeps_the_wizard_s_own_constraints():
    form = _form()
    assert form.count('maxlength="4"') == 2
    assert form.count('inputmode="numeric"') == 2


# ── the words ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_hint_exists_in_every_language(path):
    d = json.loads(path.read_text(encoding="utf-8"))["translations"]
    assert d.get("account_pin_hint"), f"{path.stem} is missing account_pin_hint"


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_words_it_borrows_all_exist(path):
    """The form reuses six strings that were already written rather than inventing new ones — but a
    reused key is only free while it is still there in every language."""
    d = json.loads(path.read_text(encoding="utf-8"))["translations"]
    for key in ("setup_pin", "setup_pin_ph", "auth_reveal", "auth_mismatch", "save"):
        assert d.get(key), f"{path.stem} is missing the borrowed key {key}"
    assert d.get("account_pin_confirm"), f"{path.stem} is missing account_pin_confirm"


def test_the_confirm_label_is_not_the_password_one():
    """Reusing a string is free until it carries grammar. `auth_confirm` was written for «la
    password», and four of the seven languages agree with it: Italian said «Ripetila», Dutch
    «Herhaal het», Polish «Powtórz je», Portuguese «Repete-a» — all beside a masculine PIN. Only
    visible by looking at the page; every test was green."""
    assert "t('account_pin_confirm')" in _form()
    assert "t('auth_confirm')" not in _form()


def test_the_hint_says_the_account_need_not_be_unlinked():
    """The whole point of the question. If the sentence does not say it, the person still presses
    Logout — which is exactly what they were doing before."""
    d = json.loads((ROOT / "web" / "locales" / "en.json").read_text(encoding="utf-8"))["translations"]
    assert "unlink" in d["account_pin_hint"].lower()
