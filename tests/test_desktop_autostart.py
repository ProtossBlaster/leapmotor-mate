"""The "start at login" switch — desktop app only.

Mate's side of this is deliberately thin: it records a yes/no and stops there. Registering a
program to run at login is a LaunchAgent on macOS and a registry entry on Windows, and neither
belongs in code that also runs inside Home Assistant — where the whole question is meaningless,
because Mate is already running whether anyone is looking or not.

So these tests pin down two things: that the answer is stored in the one place the shell reads
it from, and that the switch never appears anywhere the shell isn't there to act on it.
"""
import asyncio

import pytest

import db as D
import db_reader


class _Req:
    def __init__(self, data):
        self._data = data

    async def form(self):
        return self._data


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    path = str(tmp_path / "t.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return main


def shows(shell_version, demo=False):
    """The template's condition, kept here so a change in one can't drift from the other."""
    return bool(shell_version) and not demo


def test_the_switch_stores_the_answer(env):
    asyncio.run(env.save_desktop_autostart(_Req({"desktop_autostart": "1"})))
    assert db_reader.get_setting("desktop_autostart", "0") == "1"


def test_unchecking_stores_a_no_rather_than_nothing(env):
    """An unchecked box submits no field at all. Leaving the setting untouched would mean the
    switch could be turned on but never off — the shell would keep reading yesterday's yes."""
    asyncio.run(env.save_desktop_autostart(_Req({"desktop_autostart": "1"})))
    asyncio.run(env.save_desktop_autostart(_Req({})))
    assert db_reader.get_setting("desktop_autostart", "0") == "0"


def test_the_default_is_off(env):
    """Nobody has an app added to their login items without asking for it."""
    assert db_reader.get_setting("desktop_autostart", "0") == "0"


def test_the_stored_value_is_what_the_shell_looks_for(env):
    """The shell reads this exact key, from this exact table, and treats "1" as yes. If either
    side is renamed, this is what catches it before a user finds the switch does nothing."""
    asyncio.run(env.save_desktop_autostart(_Req({"desktop_autostart": "on"})))
    row = db_reader._get().execute(
        "SELECT value FROM settings WHERE key='desktop_autostart'").fetchone()
    assert row is not None and row[0] == "1"


def test_never_offered_on_home_assistant_or_docker(env):
    """No shell version → nothing exists that could act on the answer, so the switch would be a
    dead control. Mate is already running around the clock there anyway."""
    assert shows("") is False
    assert shows(None) is False


def test_offered_in_the_app(env):
    assert shows("1.0.0") is True


def test_not_offered_in_the_demo(env):
    """The demo runs on a throwaway database — the answer would be written there and lost."""
    assert shows("1.0.0", demo=True) is False


def test_every_language_has_the_strings(env):
    import i18n
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT"):
        t = i18n.get_t(lang)
        for key in ("desktop_section", "desktop_autostart", "desktop_autostart_desc",
                    "desktop_autostart_on", "desktop_autostart_off", "desktop_version"):
            assert t(key) != key, f"{lang} is missing {key}"
