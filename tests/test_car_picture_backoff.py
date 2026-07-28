"""The car picture must never hammer the account the rest of Mate depends on (#177 @arnolds77).

His log: 42 `Session reset` and 35 `Information verification failed` (the cloud's `code 39` throttle)
in 45 minutes on a brand-new install. Cause: the disk-cache short-circuit in /api/car-picture can
only fire once a package has been cached at least ONCE. Where the download has never succeeded there
is no file, so every request fell through to the cloud — two attempts, each resetting the session —
and a failed fetch answers 404, which the browser does not cache, so the 30 s hero refresh asked
again, forever. The image is decoration; the session it was burning is not.

These tests pin the back-off: one attempt, then silence until the window passes — while a manual
?refresh=1 (the user asking, in person) still goes straight to the cloud.
"""
import asyncio
import pathlib

import pytest

# web.main pulls in fastapi, which the minimal CI test env doesn't install — same guard the other
# main-based tests use. Without it the whole run dies at COLLECTION, not just this file.
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")

import main  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent   # not the cwd: pytest may be run from anywhere


class _Cloud:
    """Stands in for the package download. `body` None = the cloud refusing, as in #177."""

    def __init__(self, body=None):
        self.body, self.calls = body, 0

    def __call__(self):
        self.calls += 1
        return self.body


def _fresh_install(monkeypatch, tmp_path, cloud, now=1000.0):
    """A install where the package has NEVER been downloaded: the pkg path points into an empty
    tmp dir, so the `os.path.exists` short-circuit cannot fire — the case the back-off is for."""
    monkeypatch.setattr(main, "_car_picture_pkg_path", lambda: str(tmp_path / "car_picture_pkg.zip"))
    monkeypatch.setattr(main, "_car_picture_cache_path", lambda: str(tmp_path / "car_picture.png"))
    monkeypatch.setattr(main.command_client, "get_car_picture_package", cloud)
    # The success path composes the image from live status — stubbed so these tests never touch a
    # database (the container running them has a real one, which would mask the dependency).
    monkeypatch.setattr(main.db_reader, "get_latest_status", lambda: {})
    monkeypatch.setattr(main.car_image, "compose", lambda pkg, status: (b"png-bytes", "image/png"))
    monkeypatch.setattr(main, "_car_pic_boot_refresh", True)
    monkeypatch.setattr(main, "_car_pic_retry_after", 0.0)
    clock = {"t": now}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["t"])
    return clock


def _get(refresh=0):
    return asyncio.run(main.car_picture(refresh=refresh))


def test_a_refused_download_is_attempted_once_not_on_every_request(monkeypatch, tmp_path):
    cloud = _Cloud(None)
    _fresh_install(monkeypatch, tmp_path, cloud)

    assert _get().status_code == 404          # nothing to show — that part is unchanged
    assert cloud.calls == 1

    for _ in range(20):                       # 20 hero refreshes = 10 minutes of a page left open
        assert _get().status_code == 404
    assert cloud.calls == 1, "the cloud was called again inside the back-off window"


def test_manual_refresh_always_goes_now(monkeypatch, tmp_path):
    cloud = _Cloud(None)
    _fresh_install(monkeypatch, tmp_path, cloud)
    _get()
    assert cloud.calls == 1

    _get(refresh=1)                           # the user pressing refresh is not a storm
    assert cloud.calls == 2


def test_the_window_expires(monkeypatch, tmp_path):
    cloud = _Cloud(None)
    clock = _fresh_install(monkeypatch, tmp_path, cloud)
    _get()
    assert cloud.calls == 1

    clock["t"] += main._CAR_PIC_RETRY_S - 1   # one second early
    _get()
    assert cloud.calls == 1

    clock["t"] += 2                           # …and past it
    _get()
    assert cloud.calls == 2


def test_a_success_clears_the_back_off(monkeypatch, tmp_path):
    """A refusal must not lock out the next legitimate fetch once the cloud comes back."""
    cloud = _Cloud(None)
    clock = _fresh_install(monkeypatch, tmp_path, cloud)
    _get()
    assert cloud.calls == 1

    cloud.body = b"PK\x03\x04" + b"\x00" * 32     # cloud healthy again
    clock["t"] += main._CAR_PIC_RETRY_S + 1
    _get()
    assert cloud.calls == 2
    assert main._car_pic_retry_after == 0.0, "a good download must leave no back-off behind"


def test_the_boot_refresh_still_happens(monkeypatch, tmp_path):
    """The back-off must not swallow the once-per-restart re-download (#143 — a repainted car)."""
    cloud = _Cloud(b"PK\x03\x04" + b"\x00" * 32)
    _fresh_install(monkeypatch, tmp_path, cloud)
    _get()
    assert cloud.calls == 1
