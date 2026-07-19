"""Car-picture cache refresh (#143). The per-vehicle image package is cached on disk, but a car's
colour can change (or be provisional right after registration) — TripelJ's new B10 reads purple in the
cloud/official app yet Mate showed the white it had cached at first setup. So Mate re-downloads the
package ONCE after each (re)start, keeping the cached image if the cloud is unreachable at that moment.
A manual ?refresh=1 always re-downloads. It is NOT a continuous refresh — steady-state serves the cache.
"""
import asyncio

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi")
import main


def _wire(tmp_path, monkeypatch, *, cloud_returns=b"PKfresh", boot=True):
    pkg_path = str(tmp_path / "car_picture_pkg.zip")
    with open(pkg_path, "wb") as f:
        f.write(b"PKcached")                       # a pre-existing on-disk cache
    monkeypatch.setattr(main, "_car_picture_pkg_path", lambda: pkg_path)
    monkeypatch.setattr(main, "_car_pic_boot_refresh", boot)
    main._car_image_memo.clear()
    calls = {"n": 0}

    def _dl():
        calls["n"] += 1
        return cloud_returns

    monkeypatch.setattr(main.command_client, "get_car_picture_package", _dl)
    monkeypatch.setattr(main.db_reader, "get_latest_status", lambda: {})
    seen = {}

    def _compose(pkg, status):
        seen["pkg"] = pkg                          # which bytes actually got composed/served
        return (b"img", "image/png")

    monkeypatch.setattr(main.car_image, "compose", _compose)
    return pkg_path, calls, seen


def test_refreshes_once_after_boot_then_uses_cache(tmp_path, monkeypatch):
    _, calls, seen = _wire(tmp_path, monkeypatch)
    asyncio.run(main.car_picture())               # first request after a (re)start → re-download
    assert calls["n"] == 1 and seen["pkg"] == b"PKfresh"
    asyncio.run(main.car_picture())               # subsequent requests → disk cache, no new download
    assert calls["n"] == 1


def test_boot_refresh_keeps_cache_when_cloud_down(tmp_path, monkeypatch):
    _, calls, seen = _wire(tmp_path, monkeypatch, cloud_returns=None)   # cloud unreachable
    asyncio.run(main.car_picture())
    assert calls["n"] == 1                         # it tried the refresh once…
    assert seen["pkg"] == b"PKcached"             # …then kept the cached image (never goes blank)


def test_steady_state_serves_cache_without_download(tmp_path, monkeypatch):
    _, calls, seen = _wire(tmp_path, monkeypatch, boot=False)
    asyncio.run(main.car_picture())
    assert calls["n"] == 0 and seen["pkg"] == b"PKcached"   # no continuous polling


def test_manual_refresh_always_redownloads(tmp_path, monkeypatch):
    _, calls, seen = _wire(tmp_path, monkeypatch, boot=False)
    asyncio.run(main.car_picture(refresh=1))
    assert calls["n"] == 1 and seen["pkg"] == b"PKfresh"
