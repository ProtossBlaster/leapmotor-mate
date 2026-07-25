"""The version badge tells the truth about who applies the update — GitHub #desktop-app.

Mate reaches users three ways, and only two of them expect the user to go and fetch a release:

  * Home Assistant  — the Supervisor updates the add-on.
  * Docker          — the user pulls the image, so the releases page is genuinely the next step.
  * The desktop app — it downloads the new version by itself on the next launch, so pointing the
                      user at GitHub offers a job already done, and in a native window it throws
                      them into a browser for nothing.

The desktop behaviour is keyed on MATE_DESKTOP, which only the app's launcher sets. Everything
here therefore also pins the OTHER two down: with the variable absent, the status must come back
byte-for-byte as it always has. That is the half worth guarding — a regression there would change
what every existing HA and Docker user sees.
"""
import pytest

import db as D
import db_reader
import update_check


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "u.db")
    db = D.Database(path)
    db._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('update_latest','2.9.0')")
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(update_check, "_maybe_refresh", lambda: None)
    return path


def test_home_assistant_and_docker_are_untouched(store, monkeypatch):
    """No MATE_DESKTOP → exactly the old shape: available, and a link to the releases page."""
    monkeypatch.delenv("MATE_DESKTOP", raising=False)
    s = update_check.get_update_status("2.8.9")
    assert s["available"] is True
    assert s["latest"] == "2.9.0"
    assert s["url"] == update_check._RELEASES_PAGE
    assert s["desktop"] is False and s["blocked"] is None


def test_the_app_drops_the_link(store, monkeypatch):
    """In the app there is nothing to go and fetch — it already will, on the next launch."""
    monkeypatch.setenv("MATE_DESKTOP", "1")
    monkeypatch.delenv("MATE_UPDATE_BLOCKED", raising=False)
    s = update_check.get_update_status("2.8.9")
    assert s["available"] is True
    assert s["desktop"] is True
    assert s["url"] is None
    assert s["blocked"] is None


def test_a_refused_update_keeps_the_link_and_names_the_version(store, monkeypatch):
    """The one case the user must act on: the app is too old to run that release."""
    monkeypatch.setenv("MATE_DESKTOP", "1")
    monkeypatch.setenv("MATE_UPDATE_BLOCKED", "2.9.0")
    monkeypatch.delenv("MATE_DESKTOP_DOWNLOAD_URL", raising=False)
    s = update_check.get_update_status("2.8.9")
    assert s["blocked"] == "2.9.0"
    assert s["url"] == update_check._RELEASES_PAGE       # here the download IS the next step
    assert s["available"] is True


def test_the_shell_decides_where_its_own_download_lives(store, monkeypatch):
    """The app is released from its own repository, on its own schedule — an address Mate has no
    way to know. The shell passes it in, so moving the app never needs a Mate release to correct
    a link, and the badge never sends anyone to a page with no app on it."""
    monkeypatch.setenv("MATE_DESKTOP", "1")
    monkeypatch.setenv("MATE_UPDATE_BLOCKED", "2.9.0")
    monkeypatch.setenv("MATE_DESKTOP_DOWNLOAD_URL", "https://github.com/ProtossBlaster/mate-desktop/releases/latest")
    s = update_check.get_update_status("2.8.9")
    assert s["url"] == "https://github.com/ProtossBlaster/mate-desktop/releases/latest"


def test_the_download_address_is_ignored_outside_the_app(store, monkeypatch):
    """A stray variable in a Docker environment must not repoint Home Assistant's or Docker's
    badge at somewhere those users can do nothing with."""
    monkeypatch.delenv("MATE_DESKTOP", raising=False)
    monkeypatch.setenv("MATE_DESKTOP_DOWNLOAD_URL", "https://example.invalid/nope")
    s = update_check.get_update_status("2.8.9")
    assert s["url"] == update_check._RELEASES_PAGE


def test_a_refused_update_shows_even_when_versions_look_equal(store, monkeypatch):
    """The refusal must surface regardless of the version comparison.

    The app stays on the version it could run, so the plain comparison can go quiet while the
    user is in fact stuck — and silently stuck forever is the failure this badge exists to stop.
    """
    monkeypatch.setenv("MATE_DESKTOP", "1")
    monkeypatch.setenv("MATE_UPDATE_BLOCKED", "2.9.0")
    s = update_check.get_update_status("2.9.0")          # comparison alone would say "up to date"
    assert s["available"] is True
    assert s["blocked"] == "2.9.0"


def test_no_update_available_stays_quiet_in_the_app(store, monkeypatch):
    monkeypatch.setenv("MATE_DESKTOP", "1")
    monkeypatch.delenv("MATE_UPDATE_BLOCKED", raising=False)
    s = update_check.get_update_status("2.9.0")
    assert s["available"] is False


def test_an_empty_blocked_value_is_not_a_block(store, monkeypatch):
    """The launcher only sets the variable when there IS a block; an empty one must not count."""
    monkeypatch.setenv("MATE_DESKTOP", "1")
    monkeypatch.setenv("MATE_UPDATE_BLOCKED", "")
    s = update_check.get_update_status("2.8.9")
    assert s["blocked"] is None
    assert s["url"] is None
