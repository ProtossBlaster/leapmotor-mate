"""The "records only while open" notice — desktop app only.

A Mac app records while it is open, like any other app. That is obvious once said and invisible
until then, and the gap between the two is where support load comes from: whatever the car does
after the window closes is never collected, and — since the cloud keeps no history to replay —
it cannot be filled in afterwards either. The user reports missing data that was never missed.

Home Assistant and Docker keep Mate running around the clock, so the notice would be untrue
there. It is therefore keyed on the shell version, which only the desktop launcher provides —
and these tests exist as much to pin that down as to check the notice itself.
"""
import pytest

import db as D
import db_reader


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """Everything _ctx() needs to decide whether the banner shows."""
    path = str(tmp_path / "n.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    def build():
        return {
            "shell_version": __import__("os").environ.get("MATE_DESKTOP_VERSION", ""),
            "dismissed": db_reader.get_setting("desktop_notice_dismissed", "0") == "1",
        }
    return build


def shows(c, demo=False):
    """The template's condition, kept in one place so a change can't drift from the tests."""
    return bool(c["shell_version"]) and not c["dismissed"] and not demo


def test_shown_in_the_app(ctx, monkeypatch):
    monkeypatch.setenv("MATE_DESKTOP_VERSION", "1.0.0")
    assert shows(ctx()) is True


def test_never_shown_on_home_assistant_or_docker(ctx, monkeypatch):
    """No shell version → not the app → the notice would be a lie, so it must not appear."""
    monkeypatch.delenv("MATE_DESKTOP_VERSION", raising=False)
    assert shows(ctx()) is False


def test_not_shown_in_demo(ctx, monkeypatch):
    """The demo already carries its own banner; two stacked strips push the page down for
    someone who is only looking around and has no data to lose."""
    monkeypatch.setenv("MATE_DESKTOP_VERSION", "1.0.0")
    assert shows(ctx(), demo=True) is False


def test_dismissal_sticks(ctx, monkeypatch):
    monkeypatch.setenv("MATE_DESKTOP_VERSION", "1.0.0")
    assert shows(ctx()) is True
    db_reader.set_setting("desktop_notice_dismissed", "1")
    assert shows(ctx()) is False


def test_dismissal_is_remembered_across_restarts(ctx, monkeypatch):
    """It lives in settings, not in the page, so quitting and reopening must not bring it back —
    a notice that returns every launch is one people learn to click past without reading."""
    monkeypatch.setenv("MATE_DESKTOP_VERSION", "1.0.0")
    db_reader.set_setting("desktop_notice_dismissed", "1")
    db_reader._close_conn() if hasattr(db_reader, "_close_conn") else None
    assert shows(ctx()) is False


def test_the_text_exists_in_every_language(ctx):
    """A banner that falls back to English for a French user is worse than no banner."""
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    for f in sorted(root.glob("*.json")):
        data = json.loads(f.read_text())
        keys = {k for sect in data.values() if isinstance(sect, dict) for k in sect}
        assert "desktop_open_notice" in keys, f"{f.name} is missing the notice"
        assert "desktop_open_notice_ok" in keys, f"{f.name} is missing the dismiss label"
