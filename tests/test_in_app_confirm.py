"""Asking before doing, in Mate's own dialog rather than the browser's.

The browser's confirm() names the address it is served from. On Home Assistant that reads
"192.168.1.50 says"; in the desktop app it reads "127.0.0.1:4000 says", which is worse, because
that app exists precisely so nobody has to know there is a web page inside it. Neither is
something a person should have to read in order to answer "unlock the car?".

htmx raises an htmx:confirm event just before it would call confirm(), so ONE listener in
base.html covers every hx-confirm in the application. That is the design, and it is also the
fragile part: the listener cancels the event and is then responsible for issuing the request
itself. Forget that and every confirmed action silently does nothing.
"""
import json
import pathlib
import re

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
TEMPLATES = WEB / "templates"
BASE = (TEMPLATES / "base.html").read_text(encoding="utf-8")


def _without_comments(text: str) -> str:
    """Blank out HTML and JavaScript comments, keeping every newline so line numbers still point
    at the right place.

    Written after the first version — which just skipped lines starting with `//` or `<!--` —
    reported this file's own explanation of why confirm() is gone as a use of confirm(). A comment
    that spans lines does not start with its opening marker on every one of them.
    """
    def blank(m):
        return "".join(c if c == "\n" else " " for c in m.group(0))
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)      # HTML
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)       # JS block
    return re.sub(r"//[^\n]*", blank, text)                    # JS line


def test_the_dialog_is_in_the_shared_layout():
    """In base.html, so it is on every page. A dialog present on some pages and not others fails
    exactly where it is needed and nowhere it is tested."""
    assert 'id="mate-confirm"' in BASE
    assert 'id="mate-confirm-text"' in BASE
    assert 'id="mate-confirm-yes"' in BASE
    assert 'id="mate-confirm-no"' in BASE


def test_htmx_confirmations_are_intercepted_and_released():
    """Both halves. Cancelling the event without issuing the request is the failure that would
    turn every confirmed action into a no-op — and it would look like the car ignoring commands."""
    assert "htmx:confirm" in BASE, "nothing takes over htmx's confirmation"
    assert "evt.preventDefault()" in BASE, "the native dialog is not suppressed"
    assert "issueRequest(true)" in BASE, "the held request is never released"


def test_no_template_calls_the_browsers_own_confirm():
    """The regression guard, and the reason this file exists. One hx-confirm added with a hand
    written confirm() beside it puts the address bar back in front of the user, on one page only,
    where nobody would look for it."""
    offenders = []
    # A call, not the word: `confirm(` preceded by anything other than a dot or a letter, which
    # excludes mateConfirm( and member access.
    call = re.compile(r"(?<![.\w])confirm\s*\(")
    for path in TEMPLATES.rglob("*.html"):
        for n, line in enumerate(_without_comments(path.read_text(encoding="utf-8")).splitlines(), 1):
            if call.search(line):
                offenders.append(f"{path.relative_to(WEB)}:{n}: {line.strip()[:70]}")
    assert not offenders, "native confirm() calls found:\n  " + "\n  ".join(offenders)


def test_every_confirmation_still_asks_something():
    """hx-confirm with an empty question would sail straight through: the listener returns early
    when there is no question, which is right for elements that have no attribute at all — and
    silently wrong for one whose translation went missing."""
    empty = []
    for path in TEMPLATES.rglob("*.html"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r'hx-confirm\s*=\s*"([^"]*)"', line):
                if not m.group(1).strip():
                    empty.append(f"{path.relative_to(WEB)}:{n}")
    assert not empty, "hx-confirm with nothing to ask: " + ", ".join(empty)


@pytest.mark.parametrize("locale", sorted(p.name for p in (WEB / "locales").glob("*.json")))
def test_the_buttons_are_translated(locale):
    """A dialog whose buttons fall back to English is worse than the browser's, which at least
    speaks the system language."""
    data = json.loads((WEB / "locales" / locale).read_text(encoding="utf-8"))
    keys = {k for sect in data.values() if isinstance(sect, dict) for k in sect}
    assert "confirm_proceed" in keys, f"{locale} has no label for the confirm button"
    assert "cancel" in keys, f"{locale} has no label for the cancel button"


def test_the_window_slider_reverts_when_the_answer_is_no():
    """The one place that cannot just be handed to htmx. The slider has already moved by the time
    we ask, so cancelling has to put it back — and with a dialog that no longer blocks, that
    revert can only happen in a callback. Written as `if (!confirm(...)) { revert }` it would
    revert instantly, every time, before the user had answered anything."""
    commands = (TEMPLATES / "commands.html").read_text(encoding="utf-8")
    assert "mateConfirm(WINS_CONFIRM" in commands
    body = commands[commands.index("function winsSet"):]
    body = body[:body.index("\n}")]
    assert "revert" in body, "no cancel branch: the slider would stay where it was dragged"
