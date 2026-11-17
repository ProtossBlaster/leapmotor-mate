"""Summer and winter tyres, each set with its own kilometres (D#493, @DanielBrol).

A driver who swaps tyres twice a year chooses two days — winter tyres on, summer tyres on — and
Mate counts the kilometres of each set from the odometer it already stores, every season of the
history included. Off unless switched on; the setting belongs to the selected car.
"""
from datetime import date

import pytest

VIN, OTHER = "LFZTYRE000000001", "LFZTYRE000000002"


@pytest.fixture
def car(tmp_path, monkeypatch):
    import db as D
    import db_reader
    import maintenance

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id,vin,car_type) VALUES (1,?,'B10')", (VIN,))
    c.execute("INSERT INTO vehicles (id,vin,car_type) VALUES (2,?,'T03')", (OTHER,))
    c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('timezone','Europe/Rome')")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_active_vehicle(VIN)

    def odo(day, km, hour=12, vid=1):
        conn = db_reader._conn_rw()
        conn.execute("INSERT INTO positions (vehicle_id,recorded_at,soc,charging,speed_kmh,odometer_km)"
                     " VALUES (?,?,50,0,0,?)", (vid, f"{day}T{hour:02d}:00:00+00:00", km))
        conn.commit()
    return maintenance, odo


def test_off_by_default_shows_nothing(car):
    maintenance, odo = car
    odo("2026-01-10", 1000)
    assert maintenance.get_tyre_seasons() == (False, "", "")
    assert maintenance.tyre_seasons(1, date(2026, 11, 17)) is None


@pytest.mark.parametrize("winter, summer", [("", "04-15"), ("11-15", ""), ("11-15", "11-15"),
                                            ("02-29", "04-15"), ("13-01", "04-15"), ("nonsense", "04-15")])
def test_a_switch_on_without_two_different_real_days_is_not_saved(car, winter, summer):
    maintenance, _ = car
    assert maintenance.set_tyre_seasons(True, winter, summer) is not None
    assert maintenance.get_tyre_seasons() == (False, "", "")


def test_switched_off_keeps_the_days_and_shows_nothing(car):
    maintenance, odo = car
    odo("2026-01-10", 1000)
    assert maintenance.set_tyre_seasons(True, "11-15", "04-15") is None
    assert maintenance.set_tyre_seasons(False, "11-15", "04-15") is None
    assert maintenance.get_tyre_seasons() == (False, "11-15", "04-15")
    assert maintenance.tyre_seasons(1, date(2026, 11, 17)) is None


def test_each_set_gets_the_kilometres_driven_while_it_was_on(car):
    """Winter from 15/11, summer from 15/04, over two winters and one summer."""
    maintenance, odo = car
    odo("2025-12-01", 10000)   # first reading: winter 2025/26
    odo("2026-03-01", 12000)   # +2000 winter
    odo("2026-04-14", 12500)   # +500 winter (last winter day)
    odo("2026-04-15", 12600)   # +100 summer (first summer day)
    odo("2026-10-01", 16600)   # +4000 summer
    odo("2026-11-14", 17000)   # +400 summer
    odo("2026-11-15", 17050)   # +50 winter 2026/27
    odo("2026-11-17", 17300)   # +250 winter 2026/27
    maintenance.set_tyre_seasons(True, "11-15", "04-15")

    s = maintenance.tyre_seasons(1, date(2026, 11, 17))
    assert s["current"] == "winter"
    assert s["since"] == "2026-11-15"
    assert s["season_km"] == 300
    assert s["winter_km"] == 2000 + 500 + 50 + 250
    assert s["summer_km"] == 100 + 4000 + 400
    assert s["data_from"] == "2025-12-01"


def test_the_day_is_the_local_one(car):
    """23:30 UTC on 14/04 is already 15/04 in Rome: that distance is the summer set's."""
    maintenance, odo = car
    odo("2026-04-10", 5000)
    odo("2026-04-14", 5100, hour=23)
    maintenance.set_tyre_seasons(True, "11-15", "04-15")
    s = maintenance.tyre_seasons(1, date(2026, 4, 20))
    assert (s["winter_km"], s["summer_km"]) == (0, 100)


def test_an_odometer_that_goes_back_adds_nothing(car):
    maintenance, odo = car
    odo("2026-05-01", 5000)
    odo("2026-05-02", 4000)
    odo("2026-05-03", 5100)
    maintenance.set_tyre_seasons(True, "11-15", "04-15")
    assert maintenance.tyre_seasons(1, date(2026, 5, 3))["summer_km"] == 100


def test_without_odometer_the_card_says_so_instead_of_zero(car):
    maintenance, _ = car
    maintenance.set_tyre_seasons(True, "11-15", "04-15")
    s = maintenance.tyre_seasons(1, date(2026, 11, 17))
    assert s["current"] == "winter" and s["data_from"] is None
    assert s["season_km"] is None and s["winter_km"] is None and s["summer_km"] is None


def test_the_days_belong_to_the_selected_car(car):
    import db_reader
    maintenance, odo = car
    maintenance.set_tyre_seasons(True, "11-15", "04-15")
    db_reader.set_active_vehicle(OTHER)
    assert maintenance.get_tyre_seasons() == (False, "", "")
    db_reader.set_active_vehicle(VIN)
    assert maintenance.get_tyre_seasons() == (True, "11-15", "04-15")


def test_another_cars_odometer_is_not_counted(car):
    maintenance, odo = car
    odo("2026-05-01", 5000)
    odo("2026-05-02", 5100)
    odo("2026-05-01", 90000, vid=2)
    odo("2026-05-02", 99000, vid=2)
    maintenance.set_tyre_seasons(True, "11-15", "04-15")
    assert maintenance.tyre_seasons(1, date(2026, 5, 2))["summer_km"] == 100


# ── The page and the setting, through the web app ─────────────────────────────
@pytest.fixture
def app(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from events_fixture import Car, web
    car = Car(tmp_path)
    client = web(car, monkeypatch, zone="Europe/Rome", lang="it")
    for day, km in (("2025-12-01", 10000), ("2026-04-20", 12000), ("2026-10-01", 15000)):
        car.db._conn.execute("INSERT INTO positions (vehicle_id,recorded_at,soc,charging,speed_kmh,odometer_km)"
                             " VALUES (?,?,50,0,0,?)", (car.vid, f"{day}T12:00:00+00:00", km))
    car.db._conn.commit()
    return client


def _save(client, on=True, wd="15", wm="11", sd="15", sm="04"):
    data = {"winter_day": wd, "winter_month": wm, "summer_day": sd, "summer_month": sm}
    if on:
        data["tyre_seasons_enabled"] = "1"
    return client.post("/api/settings/tyre-seasons", data=data)


def test_the_settings_card_saves_and_shows_the_days(app):
    r = _save(app)
    assert r.status_code == 200 and "Salvato" in r.text
    page = app.get("/settings").text
    assert "Gomme estive e invernali" in page
    assert 'name="tyre_seasons_enabled" value="1" checked' in page
    assert '<option value="11" selected>Novembre</option>' in page
    assert '<option value="04" selected>Aprile</option>' in page


def test_the_settings_card_names_why_nothing_was_saved(app):
    r = _save(app, sm="11")
    assert "I due giorni devono essere diversi." in r.text
    r = _save(app, sd="")
    assert "Scegli entrambi i giorni" in r.text


def test_the_maintenance_page_shows_the_card_only_when_on(app):
    assert "Gomme estive · In totale" not in app.get("/maintenance").text
    _save(app)
    page = app.get("/maintenance").text
    assert "Gomme invernali · In totale" in page and "Gomme estive · In totale" in page
    assert "Contati dal contachilometri dal 01 dic 2025" in page
    _save(app, on=False)
    assert "Gomme estive · In totale" not in app.get("/maintenance").text
