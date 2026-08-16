"""With two cars, the picture on the Overview must be the picture of the car you picked.

@cookingeek, 16/08/2026, from the first install with two real Leapmotors:

> *"Car picture on the Overview doesn't follow the selected vehicle"*

The car's image is composed live from a per-vehicle layer package the cloud serves for ONE VIN. Three
separate places carried no VIN at all, and each one alone is enough to show the wrong car:

  * the cached package on disk was a single `car_picture_pkg.zip` beside the database, so whichever
    car was selected when it was fetched overwrote the other's;
  * the composed-image memo was keyed on the BODY STATE only — two cars parked with everything shut
    have the same signature, so the second one was served the first one's picture from memory;
  * the `?v=` token on the <img> is built from that same body state, so even with both of the above
    fixed the browser would keep showing the previous car for the five minutes it caches.

Fixing one and not the others just moves where the wrong picture comes from.
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

VIN_A = "LFZTEST0000000001"
VIN_B = "LFZTEST0000000099"


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _install(tmp_path, monkeypatch):
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN_A,))
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,?,'C10')", (VIN_B,))
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    # Both cars parked, everything shut: the body state that makes the two indistinguishable.
    for vid in (1, 2):
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
                  " odometer_km, plug_connected) VALUES (?,?,55,0,0,1000,0)",
                  (vid, f"2026-08-16T08:0{vid}:00+00:00"))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setenv("DB_PATH", path)

    import car_image
    import command_client
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    main._car_image_memo.clear()
    main._car_pic_boot_refresh = False

    def _pkg_for_the_selected_car():
        v, _ = db_reader.get_vehicle()
        return b"PKG-" + (v["vin"]).encode()

    monkeypatch.setattr(command_client, "get_car_picture_package", _pkg_for_the_selected_car)
    monkeypatch.setattr(main.command_client, "get_car_picture_package", _pkg_for_the_selected_car)
    monkeypatch.setattr(car_image, "compose", lambda pkg, status: (pkg, "image/png"))
    monkeypatch.setattr(main.car_image, "compose", lambda pkg, status: (pkg, "image/png"))
    return db_reader, main


def _picture(main, db_reader, vin):
    import asyncio
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, vin)
    return asyncio.run(main.car_picture()).body


def test_the_second_car_is_not_served_the_first_one_s_picture(tmp_path, monkeypatch):
    db_reader, main = _install(tmp_path, monkeypatch)
    assert _picture(main, db_reader, VIN_A) == b"PKG-" + VIN_A.encode()
    assert _picture(main, db_reader, VIN_B) == b"PKG-" + VIN_B.encode()


def test_going_back_to_the_first_car_brings_its_own_picture_back(tmp_path, monkeypatch):
    """The memo has to be per car in BOTH directions, not just the first time."""
    db_reader, main = _install(tmp_path, monkeypatch)
    _picture(main, db_reader, VIN_A)
    _picture(main, db_reader, VIN_B)
    assert _picture(main, db_reader, VIN_A) == b"PKG-" + VIN_A.encode()


def test_each_car_keeps_its_own_package_on_disk(tmp_path, monkeypatch):
    """One file for both meant every switch re-downloaded and overwrote the other car's."""
    import os
    db_reader, main = _install(tmp_path, monkeypatch)
    _picture(main, db_reader, VIN_A)
    _picture(main, db_reader, VIN_B)
    zips = sorted(f for f in os.listdir(tmp_path) if f.endswith(".zip"))
    assert len(zips) == 2, f"one package for two cars: {zips}"


def test_the_browser_is_not_left_showing_the_previous_car(tmp_path, monkeypatch):
    """The <img> URL carries a token built from the car's state; with no car in it the browser
    serves the old picture from its own cache for the five minutes this response asks for."""
    import asyncio
    import pathlib
    import re
    db_reader, main = _install(tmp_path, monkeypatch)

    def token(vin):
        db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, vin)
        body = asyncio.run(main.overview(_Req())).body.decode()
        m = re.search(r'api/car-picture\?v=([^"&]*)', body)
        assert m, "the hero image is not on the page"
        return m.group(1)

    assert pathlib.Path(db_reader.DB_PATH).exists()
    assert token(VIN_A) != token(VIN_B)
