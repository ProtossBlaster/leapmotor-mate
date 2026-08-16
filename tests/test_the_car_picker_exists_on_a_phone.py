"""With two cars, a phone must be able to switch between them (@cookingeek, 16/08/2026).

> *"No vehicle switcher on mobile that lets you pick between [the cars]"*

Exactly right, and not "hard to find": the picker lives inside the sidebar's `hidden md:block`
container, so below 768 px it is **not rendered at all**. The mobile drawer's own header carries a
title and a close button and nothing else. On a phone, an install with two Leapmotors was stuck on
whichever car happened to be selected.

It goes in the drawer rather than the top bar: the drawer is where navigation lives on a phone, and
the top bar already holds the logo, the version and the refresh button on a 375 px screen.

With one car nothing changes anywhere — the same rule the desktop picker has always followed.
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

MOBILE_START = "<!-- Mobile sidebar header -->"
MOBILE_END = "<!-- Nav -->"
PICK = 'hx-post="api/select-vehicle"'


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _page(tmp_path, monkeypatch, *, cars):
    import asyncio

    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    for i, car_type in enumerate(cars, start=1):
        pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type, year) VALUES (?,?,?,2025)",
                          (i, f"LFZTEST000000000{i}", car_type))
    pdb._conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    return asyncio.run(main.settings_page(_Req())).body.decode()


def _mobile_block(body):
    """Just the drawer's own header block, which ends where the nav begins — so a picker that only
    exists on the desktop side of the sidebar cannot satisfy this."""
    start = body.index(MOBILE_START)
    return body[start:body.index(MOBILE_END, start)]


def test_the_drawer_carries_a_picker_when_there_are_two_cars(tmp_path, monkeypatch):
    body = _page(tmp_path, monkeypatch, cars=("B10", "C10"))
    assert PICK in _mobile_block(body), "the phone has no way to switch car"


def test_the_phone_picker_lists_every_car(tmp_path, monkeypatch):
    block = _mobile_block(_page(tmp_path, monkeypatch, cars=("B10", "C10", "T03")))
    for vin in ("LFZTEST0000000001", "LFZTEST0000000002", "LFZTEST0000000003"):
        assert vin in block, f"{vin} is missing from the phone picker"


def test_one_car_gets_no_picker_at_all(tmp_path, monkeypatch):
    """The rule the desktop picker has always had: with a single Leapmotor nothing appears."""
    body = _page(tmp_path, monkeypatch, cars=("B10",))
    assert PICK not in body


def test_the_desktop_picker_is_still_there(tmp_path, monkeypatch):
    """Two pickers, one per layout — not one moved from the wide screen to the narrow one."""
    body = _page(tmp_path, monkeypatch, cars=("B10", "C10"))
    assert body.count(PICK) == 2
