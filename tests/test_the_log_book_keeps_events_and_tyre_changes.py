"""The Maintenance page's log book, and the tyre sets counted from it (D#493, @DanielBrol).

5.0.0 counted summer and winter tyres from two fixed days repeated every year. Daniel swaps them on
a different day each year, and wants a dated record of what happens to the car: modifications,
breakdowns, damage, each with a note and a cost. So the tyre changes are entries of that record, and
each set's kilometres are counted from the day its entry says it went on. The two fixed days of an
install that used them are turned, once, into one entry per change they stood for: same totals.
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


# ── The log book ──────────────────────────────────────────────────────────────
def test_an_entry_keeps_its_date_odometer_kind_note_and_cost(car):
    maintenance, _ = car
    assert maintenance.add_event(1, "damage", "2026-09-20", 10880, "graffio paraurti", 180.0) is None
    [e] = maintenance.events(1)
    assert (e["kind"], e["date"], e["km"], e["note"], e["cost"]) == \
        ("damage", "2026-09-20", 10880, "graffio paraurti", 180.0)


def test_the_entries_come_newest_first(car):
    maintenance, _ = car
    maintenance.add_event(1, "other", "2026-01-05", 100, "", None)
    maintenance.add_event(1, "modification", "2026-06-01", 900, "", None)
    maintenance.add_event(1, "breakdown", "2026-03-01", 500, "", None)
    assert [e["kind"] for e in maintenance.events(1)] == ["modification", "breakdown", "other"]


def test_a_blank_odometer_is_the_one_mate_read_that_day(car):
    """The last reading of that local day, else of the last day before it with one."""
    maintenance, odo = car
    odo("2026-09-18", 10500)
    odo("2026-09-20", 10800, hour=8)
    odo("2026-09-20", 10880, hour=18)
    odo("2026-09-21", 11000)
    maintenance.add_event(1, "damage", "2026-09-20", None, "", None)
    maintenance.add_event(1, "other", "2026-09-19", None, "", None)
    assert [e["km"] for e in maintenance.events(1)] == [10880, 10500]


def test_without_any_reading_a_blank_odometer_stays_blank(car):
    maintenance, _ = car
    maintenance.add_event(1, "other", "2026-09-20", None, "", None)
    assert maintenance.events(1)[0]["km"] is None


@pytest.mark.parametrize("kind, day, cost", [("party", "2026-09-20", None), ("damage", "20/09/2026", None),
                                             ("damage", "2026-02-30", None), ("damage", "2026-09-20", -1.0)])
def test_a_wrong_entry_is_not_saved(car, kind, day, cost):
    maintenance, _ = car
    assert maintenance.add_event(1, kind, day, None, "", cost) is not None
    assert maintenance.events(1) == []


def test_an_entry_belongs_to_its_car_and_is_deleted_only_there(car):
    maintenance, _ = car
    maintenance.add_event(1, "damage", "2026-09-20", 100, "", None)
    maintenance.add_event(2, "damage", "2026-09-20", 200, "", None)
    mine, = maintenance.events(1)
    theirs, = maintenance.events(2)
    maintenance.delete_event(1, theirs["id"])
    assert len(maintenance.events(2)) == 1
    maintenance.delete_event(1, mine["id"])
    assert maintenance.events(1) == []


def test_a_service_of_the_programme_keeps_its_cost_and_stays_out_of_the_book(car):
    maintenance, _ = car
    maintenance.add_log(1, "cabin_filter_replace", "2026-05-01", 6000, "", 35.5)
    assert maintenance.latest_logs(1)["cabin_filter_replace"]["cost"] == 35.5
    assert maintenance.events(1) == []


# ── The tyre sets, from the book ──────────────────────────────────────────────
def test_no_tyre_entry_no_tyre_card(car):
    maintenance, odo = car
    odo("2026-01-10", 1000)
    maintenance.add_event(1, "damage", "2026-01-11", None, "", None)
    assert maintenance.tyre_seasons(1, date(2026, 11, 17)) is None


def test_each_set_gets_the_kilometres_driven_while_it_was_on_whatever_the_day(car):
    """The changes fall on a different day each year."""
    maintenance, odo = car
    odo("2025-12-01", 10000)
    odo("2026-03-01", 12000)   # +2000 winter
    odo("2026-04-02", 12500)   # +500 winter (last winter day)
    odo("2026-04-03", 12600)   # +100 summer
    odo("2026-10-01", 16600)   # +4000 summer
    odo("2026-11-02", 17000)   # +400 summer
    odo("2026-11-03", 17050)   # +50 winter
    odo("2026-11-17", 17300)   # +250 winter
    for kind, day in (("tyres_winter", "2025-11-20"), ("tyres_summer", "2026-04-03"),
                      ("tyres_winter", "2026-11-03")):
        maintenance.add_event(1, kind, day, None, "", None)

    s = maintenance.tyre_seasons(1, date(2026, 11, 17))
    assert s["current"] == "winter" and s["since"] == "2026-11-03"
    assert s["season_km"] == 300
    assert s["winter_km"] == 2000 + 500 + 50 + 250
    assert s["summer_km"] == 100 + 4000 + 400
    assert s["data_from"] == "2025-12-01"


def test_before_the_first_change_the_set_is_unknown_and_nothing_is_counted(car):
    maintenance, odo = car
    odo("2026-01-01", 1000)
    odo("2026-03-01", 3000)    # no set known yet: not counted
    odo("2026-04-10", 3100)    # +100 summer
    maintenance.add_event(1, "tyres_summer", "2026-04-10", None, "", None)
    s = maintenance.tyre_seasons(1, date(2026, 5, 1))
    assert (s["summer_km"], s["winter_km"]) == (100, 0)


def test_the_kilometres_before_mate_are_added_to_their_set(car):
    maintenance, odo = car
    odo("2026-04-09", 3000)
    odo("2026-04-10", 3100)
    maintenance.add_event(1, "tyres_summer", "2026-04-10", None, "", None)
    assert maintenance.set_tyre_prior_km(12000, 8000) is None
    s = maintenance.tyre_seasons(1, date(2026, 5, 1))
    assert (s["winter_km"], s["summer_km"]) == (12000, 8000 + 100)
    assert s["season_km"] == 100
    assert maintenance.set_tyre_prior_km(-1, 0) is not None
    assert maintenance.get_tyre_prior_km() == (12000, 8000)


def test_the_day_is_the_local_one(car):
    """23:30 UTC on 14/04 is already 15/04 in Rome: that distance is the summer set's."""
    maintenance, odo = car
    odo("2026-04-10", 5000)
    odo("2026-04-14", 5100, hour=23)
    maintenance.add_event(1, "tyres_winter", "2025-11-15", None, "", None)
    maintenance.add_event(1, "tyres_summer", "2026-04-15", None, "", None)
    s = maintenance.tyre_seasons(1, date(2026, 4, 20))
    assert (s["winter_km"], s["summer_km"]) == (0, 100)


def test_an_odometer_that_goes_back_adds_nothing(car):
    maintenance, odo = car
    odo("2026-05-01", 5000)
    odo("2026-05-02", 4000)
    odo("2026-05-03", 5100)
    maintenance.add_event(1, "tyres_summer", "2026-04-15", None, "", None)
    assert maintenance.tyre_seasons(1, date(2026, 5, 3))["summer_km"] == 100


def test_without_odometer_the_card_says_so_instead_of_zero(car):
    maintenance, _ = car
    maintenance.add_event(1, "tyres_winter", "2026-11-15", None, "", None)
    s = maintenance.tyre_seasons(1, date(2026, 11, 17))
    assert s["current"] == "winter" and s["data_from"] is None
    assert s["season_km"] is None and s["winter_km"] is None and s["summer_km"] is None


def test_another_cars_odometer_and_changes_are_not_counted(car):
    maintenance, odo = car
    odo("2026-05-01", 5000)
    odo("2026-05-02", 5100)
    odo("2026-05-01", 90000, vid=2)
    odo("2026-05-02", 99000, vid=2)
    maintenance.add_event(1, "tyres_summer", "2026-04-15", None, "", None)
    maintenance.add_event(2, "tyres_winter", "2026-05-01", None, "", None)
    s = maintenance.tyre_seasons(1, date(2026, 5, 2))
    assert (s["summer_km"], s["winter_km"]) == (100, 0)


# ── The two fixed days of 5.0.0, turned into entries once ─────────────────────
def _fixed_days(winter="11-15", summer="04-15", on="1"):
    import db_reader
    for key, value in (("tyre_seasons_enabled", on), ("tyre_winter_on", winter), ("tyre_summer_on", summer)):
        db_reader.set_setting(f"{key}_{VIN.lower()}", value)


def test_the_fixed_days_become_one_entry_per_change_with_the_same_totals(car):
    maintenance, odo = car
    for day, km in (("2025-12-01", 10000), ("2026-03-01", 12000), ("2026-04-14", 12500),
                    ("2026-04-15", 12600), ("2026-10-01", 16600), ("2026-11-14", 17000),
                    ("2026-11-15", 17050), ("2026-11-17", 17300)):
        odo(day, km)
    _fixed_days()

    maintenance.convert_tyre_days(1, date(2026, 11, 17))

    book = [(e["kind"], e["date"], e["km"]) for e in maintenance.events(1)]
    assert book == [("tyres_winter", "2026-11-15", 17050), ("tyres_summer", "2026-04-15", 12600),
                    ("tyres_winter", "2025-11-15", None)]
    s = maintenance.tyre_seasons(1, date(2026, 11, 17))
    assert (s["winter_km"], s["summer_km"], s["season_km"]) == (2800, 4500, 300)


def test_the_conversion_runs_once(car):
    import db_reader
    maintenance, odo = car
    odo("2026-05-01", 5000)
    _fixed_days()
    maintenance.convert_tyre_days(1, date(2026, 11, 17))
    maintenance.convert_tyre_days(1, date(2026, 11, 17))
    assert len(maintenance.events(1)) == 2
    assert db_reader.get_setting(f"tyre_seasons_enabled_{VIN.lower()}") != "1"


def test_days_that_were_switched_off_are_not_converted(car):
    maintenance, odo = car
    odo("2026-05-01", 5000)
    _fixed_days(on="0")
    maintenance.convert_tyre_days(1, date(2026, 11, 17))
    assert maintenance.events(1) == []


def test_without_odometer_the_conversion_records_the_set_on_the_car(car):
    maintenance, _ = car
    _fixed_days()
    maintenance.convert_tyre_days(1, date(2026, 11, 17))
    assert [(e["kind"], e["date"]) for e in maintenance.events(1)] == [("tyres_winter", "2026-11-15")]


def test_the_conversion_touches_only_the_car_whose_days_they_are(car):
    maintenance, odo = car
    odo("2026-05-01", 5000)
    _fixed_days()
    maintenance.convert_tyre_days(2, date(2026, 11, 17))
    assert maintenance.events(2) == []


# ── Through the web app ───────────────────────────────────────────────────────
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


def _add(client, **over):
    data = {"kind": "damage", "date": "2026-09-20", "km": "", "note": "graffio paraurti", "cost": "180"}
    data.update(over)
    return client.post("/api/maintenance/event", data=data)


def test_the_page_adds_shows_and_deletes_an_entry(app):
    page = app.get("/maintenance").text
    assert "Registro" in page and "Nessuna voce" in page
    r = _add(app)
    assert r.status_code == 200
    assert "Danno" in r.text and "graffio paraurti" in r.text and "180,00" in r.text
    import re
    entry_id = re.search(r'name="id" value="(\d+)"', r.text).group(1)
    r = app.post("/api/maintenance/event/delete", data={"id": entry_id})
    assert "graffio paraurti" not in r.text and "Nessuna voce" in r.text


def test_a_tyre_entry_brings_the_tyre_card(app):
    assert "Gomme invernali · In totale" not in app.get("/maintenance").text
    r = _add(app, kind="tyres_winter", date="2025-11-15", note="", cost="")
    assert "Gomme invernali montate" in r.text
    page = app.get("/maintenance").text
    assert "Gomme invernali · In totale" in page and "Gomme estive · In totale" in page


def test_the_settings_page_no_longer_asks_for_two_days(app):
    page = app.get("/settings").text
    assert 'name="tyre_seasons_enabled"' not in page


def test_the_conversion_writes_nothing_where_tyre_entries_already_are(car):
    """A database where the days are still switched on AND the book already has tyre entries (a
    restored backup, a second process): the entries are the truth, the days are only switched off."""
    import db_reader
    maintenance, odo = car
    odo("2026-05-01", 5000)
    maintenance.add_event(1, "tyres_summer", "2026-04-03", None, "", None)
    _fixed_days()
    maintenance.convert_tyre_days(1, date(2026, 11, 17))
    assert [(e["kind"], e["date"]) for e in maintenance.events(1)] == [("tyres_summer", "2026-04-03")]
    assert db_reader.get_setting(f"tyre_seasons_enabled_{VIN.lower()}") == "0"
