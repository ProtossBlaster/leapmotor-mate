"""Fixing the car the banner complains about must turn the banner off (@cookingeek, 16/08/2026).

The first Mate in the world running two real cars, on v3.14.0, the day after the strip shipped:

> *"unconfigured banner persists after fixing the battery capacity. I corrected the battery capacity
> via Settings → Battery capacity, but the banner is still showing. Separate PIN was also set."*

He is right, and it is the worst shape a notice can have: it names a thing to fix, he fixes it the
right way, and it keeps accusing him. The mark it reads, `vehicle_setup_done_<vin>`, was written in
exactly ONE place in all of Mate — the setup wizard — so the only way to silence it was to walk back
through a wizard for a car that was already correct.

The pack is what the strip is actually about: a car that walked in on its own took its MODEL's
default, and every kWh, €/kWh and consumption of that car follows it. So the answer to "nobody chose
this car's pack" is somebody choosing it, wherever they do it.

🔴 And it is stamped on the SELECTED car only. Writing it install-wide would silence the strip for a
third car nobody has looked at yet — the same mistake as writing the first car's capacity while
looking at the second (#186), which was an ~80% error on everything derived from a percentage.
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}

    def __init__(self, form=None):
        self._form = form or {}

    async def form(self):
        return self._form


def _two_cars(tmp_path, monkeypatch):
    """A B10 that was here before the update and a C10 that walked in afterwards — cookingeek's
    install, in the state that lights the strip."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    c.execute("DELETE FROM settings WHERE key = 'vehicle_setup_backfilled'")
    c.commit()
    pdb._conn.close()
    D.Database(path)._conn.close()          # the poller marks the B10 that was already here
    pdb2 = D.Database(path)
    pdb2.ensure_vehicle("LFZTEST0000000099", "C10")     # …and the C10 arrives after
    pdb2._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    return db_reader, main


def _flagged(db_reader):
    return [v["car_type"] for v in db_reader.unconfigured_vehicles()]


async def _save_capacity(main, kwh):
    return await main.capacity_settings(_Req({"battery_capacity_kwh": str(kwh)}))


def test_choosing_the_pack_answers_the_strip(tmp_path, monkeypatch):
    """His exact steps: open Settings on the car being accused, set the capacity, done."""
    import asyncio
    db_reader, main = _two_cars(tmp_path, monkeypatch)
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "LFZTEST0000000099")
    assert _flagged(db_reader) == ["C10"], "the fixture must start with the strip up"

    asyncio.run(_save_capacity(main, 81.9))
    assert _flagged(db_reader) == []


def test_it_answers_for_the_car_you_are_looking_at_and_no_other(tmp_path, monkeypatch):
    """A third car nobody has seen must keep its strip: silencing the notice install-wide would
    hide exactly the case it exists for."""
    import asyncio

    import db as D
    db_reader, main = _two_cars(tmp_path, monkeypatch)
    pdb = D.Database(db_reader.DB_PATH)
    pdb.ensure_vehicle("LFZTEST0000000077", "T03")
    pdb._conn.close()
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "LFZTEST0000000099")
    assert sorted(_flagged(db_reader)) == ["C10", "T03"]

    asyncio.run(_save_capacity(main, 81.9))
    assert _flagged(db_reader) == ["T03"]


def test_the_strip_is_gone_from_the_page_after_the_save(tmp_path, monkeypatch):
    """Through the real layout, because the strip is a claim about base.html."""
    import asyncio
    db_reader, main = _two_cars(tmp_path, monkeypatch)
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "LFZTEST0000000099")
    assert "unconfigured-bar" in asyncio.run(main.settings_page(_Req())).body.decode()

    asyncio.run(_save_capacity(main, 81.9))
    assert "unconfigured-bar" not in asyncio.run(main.settings_page(_Req())).body.decode()


def test_the_capacity_itself_still_lands_on_the_right_car(tmp_path, monkeypatch):
    """The stamp must not become the interesting part: what the form is FOR still has to work.

    The B10 is compared before and after rather than against None: every registered car is given
    its model's default by the poller (that default IS what the strip warns about), so "unchanged"
    is the claim here, not "empty"."""
    import asyncio
    db_reader, main = _two_cars(tmp_path, monkeypatch)
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "LFZTEST0000000099")
    before = {v["car_type"]: v["capacity_kwh"] for v in db_reader.get_vehicles()}
    asyncio.run(_save_capacity(main, 81.9))

    rows = {v["car_type"]: v["capacity_kwh"] for v in db_reader.get_vehicles()}
    assert rows["C10"] == 81.9
    assert rows["B10"] == before["B10"], "the other car's pack moved"


def test_giving_a_car_its_own_pin_answers_the_strip_too(tmp_path, monkeypatch):
    """The case the capacity box cannot cover: a car whose model default happens to be right has
    nothing to change in that field, so without this the strip could only be cleared by walking the
    wizard again. @cookingeek set a separate PIN and kept the strip.

    The PIN form names its car explicitly (#186), so this stamps that VIN — not the selected one."""
    import asyncio
    db_reader, main = _two_cars(tmp_path, monkeypatch)
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "LFZTEST0000000001")   # the OTHER car
    assert _flagged(db_reader) == ["C10"]

    asyncio.run(main.set_operation_pin(_Req(
        {"pin": "1234", "pin2": "1234", "vin": "LFZTEST0000000099"})))
    assert _flagged(db_reader) == []


def test_a_pin_for_the_whole_install_answers_for_nobody(tmp_path, monkeypatch):
    """No `vin` is the old install-wide PIN. It says nothing about WHICH car anyone looked at, so
    it must not silence a strip — that is how the notice would disappear for a car nobody has seen."""
    import asyncio
    db_reader, main = _two_cars(tmp_path, monkeypatch)
    asyncio.run(main.set_operation_pin(_Req({"pin": "1234", "pin2": "1234"})))
    assert _flagged(db_reader) == ["C10"]
