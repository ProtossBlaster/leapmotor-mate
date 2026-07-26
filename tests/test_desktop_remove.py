"""Removing Mate from a Mac, from inside Mate — because macOS offers nowhere else.

An app on macOS is dragged to the Bin, and the Bin has never heard of Application Support. That
is why a Mac fills up with folders belonging to programs it no longer has, and why deleting the
app leaves Mate's database, certificate and logs behind. (App Store apps are the exception people
remember: sandboxed into ~/Library/Containers, removed with the app. Mate is not one.)

So Settings gets a "remove everything" button — on the Mac only. Windows has a real uninstaller in
Settings ▸ Apps that already takes the data with it, and two ways to remove one app, with one of
them hidden inside it, is worse than one obvious way.

Mate itself deletes nothing. It exits with code 43, the sibling of the 42 it already uses to ask
for a restart, and the shell does the work: where the data lives is the shell's business, not the
business of code that also runs on Home Assistant.
"""
import json
import pathlib

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
MAIN = (WEB / "main.py").read_text(encoding="utf-8")
SETTINGS = (WEB / "templates" / "settings.html").read_text(encoding="utf-8")


def test_the_route_exists_and_uses_the_agreed_exit_code():
    """43, and nothing else. The shell watches for exactly that number; any other value means
    'a service died' and the app would simply stop with the data still there."""
    assert '"/api/settings/desktop-remove"' in MAIN
    body = MAIN[MAIN.index("async def desktop_remove_everything"):]
    body = body[:body.index("\n@app.")]
    assert "os._exit(43)" in body, "the removal never asks the shell to do anything"


def test_mate_deletes_nothing_itself():
    """The division of labour is the point. If this route ever grows an rmtree, the same code
    would be one import away from running on somebody's Home Assistant box."""
    body = MAIN[MAIN.index("async def desktop_remove_everything"):]
    body = body[:body.index("\n@app.")]
    for forbidden in ("rmtree", "os.remove", "unlink", "shutil"):
        assert forbidden not in body, f"the route removes files itself ({forbidden})"


def test_it_is_refused_outside_the_desktop_app():
    """Two gates, and both matter: no shell means nobody is listening for exit 43 — the app would
    just die — and on Windows the uninstaller already does this properly."""
    body = MAIN[MAIN.index("async def desktop_remove_everything"):]
    body = body[:body.index("\n@app.")]
    assert 'MATE_DESKTOP_VERSION' in body, "not gated on running inside the app"
    assert 'MATE_DESKTOP_PLATFORM' in body and 'macOS' in body, "not gated to macOS"
    assert body.index("MATE_DESKTOP_VERSION") < body.index("os._exit"), "gate runs after the exit"


def test_the_context_carries_the_platform():
    """The template gates on shell_platform; without it in the context the button would render
    on Windows too — where it does nothing useful and contradicts the real uninstaller."""
    assert '"shell_platform"' in MAIN
    assert 'MATE_DESKTOP_PLATFORM' in MAIN


def test_the_button_is_macos_only_and_asks_first():
    assert "shell_platform == 'macOS'" in SETTINGS, "the button is not gated to macOS"
    block = SETTINGS[SETTINGS.index("shell_platform == 'macOS'"):]
    block = block[:block.index("{% endif %}")]
    assert "api/settings/desktop-remove" in block
    assert "hx-confirm" in block, "a destructive button with no confirmation"


def test_the_button_lives_inside_the_desktop_card():
    """Which is itself gated on shell_version: on Home Assistant and Docker the whole card is
    absent, so this can never appear there however the platform variable is set."""
    card = SETTINGS[SETTINGS.index("{% if shell_version and not demo %}"):]
    card = card[:card.index("{% endcall %}")]
    assert "desktop-remove" in card


@pytest.mark.parametrize("locale", sorted(p.name for p in (WEB / "locales").glob("*.json")))
def test_every_string_is_translated(locale):
    data = json.loads((WEB / "locales" / locale).read_text(encoding="utf-8"))
    keys = {k for sect in data.values() if isinstance(sect, dict) for k in sect}
    for k in ("desktop_remove", "desktop_remove_desc", "desktop_remove_btn",
              "desktop_remove_confirm", "desktop_remove_doing"):
        assert k in keys, f"{locale} is missing {k}"


@pytest.mark.parametrize("locale", sorted(p.name for p in (WEB / "locales").glob("*.json")))
def test_the_confirmation_says_it_cannot_be_undone(locale):
    """The one string that has to carry weight. Someone reading it is one click from losing years
    of driving history, and 'are you sure?' is not enough warning for that."""
    data = json.loads((WEB / "locales" / locale).read_text(encoding="utf-8"))
    flat = {k: v for sect in data.values() if isinstance(sect, dict) for k, v in sect.items()}
    msg = flat["desktop_remove_confirm"]
    assert len(msg) > 80, f"{locale}: the warning is too short to warn"
    # Every language mentions the backup route out, in its own words; checking for the section
    # name is the one token they all share.
    assert "ackup" in msg or "opia" in msg or "auvegarde" in msg or "xport" in msg, \
        f"{locale}: does not point at the backup before destroying anything"
