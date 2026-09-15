"""A T03 west of Greenwich must stay west — GitHub #282.

Every car that ever landed in the sea before (#30, #43, #158, #203, #232) was a C10 or B10: a
numeric `signal` dict, where 2/3 carry the sign and 3724/3725 are bare magnitudes. The T03 has no
such dict. Its cloud sends `latitude` / `longitude` as named fields, ALREADY SIGNED, and the adapter
filed them only under 3724/3725 — the magnitude slots. The signed slots were mapped to
`latitudeSigned` / `longitudeSigned`, names that exist nowhere: not in leapmotor-api 0.3.1, not in
any fixture, not in any response. So on a T03 the signed pair never arrived, every coordinate went
through `abs(u) × remembered sign`, and the remembered sign could only ever be learned from a
history already written with the minus dropped.

Coooogz's T03: home ~30 km west of the meridian, work 100 m east of it. The drive to work was drawn
as 30 km heading EAST. The bundle showed the longitude arriving negative and the trip starts logged
positive. Nothing east of Greenwich could see it — that is every T03 we had.

Coordinates below are round, public places, not anyone's address.
"""
import sqlite3

import client
import command_client
import db as D
import db_reader
import diagnostics


def _t03(lat: float, lon: float) -> dict:
    """The `data` block of a T03 status response: named fields at the top level, no `signal`."""
    return {"latitude": lat, "longitude": lon, "soc": 81, "gearStatus": 3, "speed": 42.0,
            "totalMileage": 255}


WEST = _t03(51.7520, -1.2577)       # Oxford — 1.26° west, well away from the line
EAST = _t03(45.443407, 9.124942)    # Milan — every T03 we had before #282


def _poller_sig(frame: dict) -> dict:
    sig = client._named_fields_to_signal(frame)
    assert sig is not None
    return sig


def _web_sig(frame: dict) -> dict:
    sig = command_client._named_fields_to_signal(frame)
    assert sig is not None
    return sig


def _web_db(tmp_path, monkeypatch, *, lon_sign, name):
    path = str(tmp_path / f"{name}.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN','T03')")
    for axis, val in (("lat", 1.0), ("lon", lon_sign)):
        db._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                         (f"gps_{axis}_sign", str(val)))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _last_lon(path) -> float:
    con = sqlite3.connect(path)
    lon = con.execute("SELECT longitude FROM positions ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    return lon


def test_the_poller_stores_a_t03_west_of_greenwich_west():
    """The #282 install exactly: the sign re-derived from a history written without its minus is
    +1, and the car reports itself west. Before the fix this came out +1.2577."""
    client.seed_coord_signs("VIN282A", 1.0, 1.0)
    data = client._parse_signal("VIN282A", _poller_sig(WEST))
    assert data.longitude == -1.2577
    assert data.latitude == 51.7520


def test_a_fresh_t03_install_west_of_greenwich_starts_west():
    """No sign remembered at all — the first poll after the wizard. The value carries its own sign,
    so there is nothing to guess and nothing to wait for."""
    data = client._parse_signal("VIN282B", _poller_sig(WEST))
    assert data.longitude == -1.2577


def test_a_t03_east_of_greenwich_does_not_move():
    data = client._parse_signal("VIN282C", _poller_sig(EAST))
    assert (data.latitude, data.longitude) == (45.443407, 9.124942)


def test_the_refresh_button_files_a_t03_row_west(tmp_path, monkeypatch):
    """The web's own copy of the adapter never mapped the signed slots at all, so the Refresh
    button re-applied the remembered +1 to every T03 reading — the newest row, the one the map
    draws, filed on the wrong side."""
    path = _web_db(tmp_path, monkeypatch, lon_sign=1.0, name="t03w")
    db_reader.save_fresh_signals(_web_sig(WEST))
    assert _last_lon(path) == -1.2577


def test_the_drive_from_home_to_work_and_back_never_changes_side(tmp_path, monkeypatch):
    """Coooogz's route in miniature: home west, across the meridian, work 100 m east, and back —
    through the poller AND the Refresh button, starting from the +1 that install's history taught. Every
    point must land on the side it is really on; before the fix five of seven were mirrored."""
    route = [-0.4500, -0.2000, -0.0100, 0.0015, 0.0020, -0.0500, -0.4500]
    client.seed_coord_signs("VIN282D", 1.0, 1.0)
    path = _web_db(tmp_path, monkeypatch, lon_sign=1.0, name="route")
    polled, refreshed = [], []
    for lon in route:
        frame = _t03(51.4779, lon)
        polled.append(client._parse_signal("VIN282D", _poller_sig(frame)).longitude)
        db_reader.save_fresh_signals(_web_sig(frame))
        refreshed.append(_last_lon(path))
    assert polled == route
    assert refreshed == route


def test_the_poller_and_the_web_read_a_t03_with_the_same_map():
    """Two copies of one adapter, in two processes that cannot import each other. They had
    drifted: the poller had the signed slots, the web did not. And the signed slots must read the
    fields the T03 really sends — the same ones the magnitude slots read."""
    assert command_client._SIGNAL_TO_NAMED == client._SIGNAL_TO_NAMED
    for m in (client._SIGNAL_TO_NAMED, command_client._SIGNAL_TO_NAMED):
        assert m["2"] == m["3724"] == "longitude"
        assert m["3"] == m["3725"] == "latitude"


def test_the_bundle_still_carries_no_t03_coordinate():
    """The signed slots are now filled on a T03 too, and the raw-signal section of the diagnostics
    bundle is attached in public. No coordinate may reach it through the new keys."""
    section = diagnostics._signals_section(_web_sig(WEST), None)
    assert "1.2577" not in section and "51.752" not in section
