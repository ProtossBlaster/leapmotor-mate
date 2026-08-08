"""#237 — the kilometres a charge carries, and the window the cost card divides over.

@nico89612 typed 152 charges into Mate from before he installed it, and the Statistics page told
him **4838.43 €/100 km**. Nothing was rounded wrong: the euros were summed over the whole archive
while the divisor came from the three trips the poller had managed to record — months of spending
over 46 km of one afternoon. The same card's kWh/100 km half was already windowed correctly, and
reproducing his 19.2 to the decimal is what proved the two halves were describing different years.

What is fixed here, and what is NEW:

  · the euros are counted over the window the KILOMETRES cover, never wider;
  · `charges.odometer_km` — written by the poller from the frame that opens a charge, typed on a
    manual one, back-filled once from `positions` for sessions already recorded;
  · the divisor may be the car's own odometer instead of Mate's reconstructed trips, but only when
    that prices MORE of what was actually spent — which is exactly the case the trips cannot speak
    for, and never an ordinary history;
  · re-importing a CSV FILLS IN the sessions already there instead of adding them a second time
    (before this, 152 charges became 304 in silence);
  · how far the car went between one charge and the next, which falls out of the column for free.
"""
import sqlite3
import types

import charge_import
import db as D
import db_reader
import pytest


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','B10')")
    database.set_setting("battery_capacity_kwh", "50.0")
    database._conn.commit()
    return database


def _frame(soc=50.0, odo=1000.0, lat=45.0, lon=9.0):
    return types.SimpleNamespace(soc=soc, odometer_km=odo, latitude=lat, longitude=lon)


def _trip(db, day, km=100.0, soc=(80.0, 60.0)):
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (1,?,?,?,?,?)",
        (f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T18:00:00+00:00",
         km, soc[0], soc[1]))
    db._conn.commit()


def _charge(db, day, kwh=20.0, cost=5.0, odo=None, hour=10):
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, cost, odometer_km) VALUES (1,?,?,20,60,?,?,?)",
        (f"2026-07-{day:02d}T{hour:02d}:00:00+00:00", f"2026-07-{day:02d}T{hour + 1:02d}:00:00+00:00",
         kwh, cost, odo))
    db._conn.commit()


# ── B · the poller stamps the odometer ────────────────────────────────────────

def test_a_charge_records_the_odometer_it_started_at(car):
    cid = car.create_charge(1, _frame(odo=12345.0))
    row = car._conn.execute("SELECT odometer_km FROM charges WHERE id=?", (cid,)).fetchone()
    assert row["odometer_km"] == 12345.0


def test_an_absent_odometer_is_not_kilometre_zero(car):
    """`client` reads signal 1318 as `float(sig.get("1318") or 0)`, so a frame that carries no
    odometer arrives as 0.0. Stored, that would say the session happened at the factory gate — a
    wrong number where the truth is "unknown". → [[signal-absent-is-not-signal-zero]]"""
    cid = car.create_charge(1, _frame(odo=0.0))
    assert car._conn.execute(
        "SELECT odometer_km FROM charges WHERE id=?", (cid,)).fetchone()["odometer_km"] is None


def test_a_reconstructed_charge_carries_it_too(car):
    """The car was asleep, so nothing was polled during the charge — but a PARKED car's odometer
    does not move, so the reading now is the reading then."""
    cid = car.create_reconstructed_charge(1, 30.0, "2026-07-01T02:00:00+00:00", _frame(soc=80.0, odo=999.0))
    assert car._conn.execute(
        "SELECT odometer_km FROM charges WHERE id=?", (cid,)).fetchone()["odometer_km"] == 999.0


# ── B · the one-off back-fill out of `positions` ──────────────────────────────

def _position(db, ts, odo):
    db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, odometer_km) VALUES (1,?,?)", (ts, odo))
    db._conn.commit()


def _rerun_backfill(db):
    db.set_setting("charges_odometer_backfill_v1", "0")
    db._backfill_charge_odometer()


def test_the_backfill_copies_the_odometer_the_same_poll_wrote(car):
    """Measured on a real B10: 26 of 28 charges matched, worst offset 0.0 minutes — because
    `create_charge` and `save_position` run off ONE frame."""
    _charge(car, day=3, odo=None)
    _position(car, "2026-07-03T10:00:00+00:00", 4321.0)
    _rerun_backfill(car)
    assert car._conn.execute("SELECT odometer_km FROM charges").fetchone()["odometer_km"] == 4321.0


def test_the_backfill_will_not_reach_for_another_afternoon(car):
    """⚠️ A generous window would look like better coverage while stamping the wrong day's
    kilometres. The right match is seconds away; anything hours away is not a near miss."""
    _charge(car, day=3, odo=None)
    _position(car, "2026-07-03T16:00:00+00:00", 4321.0)      # six hours later
    _rerun_backfill(car)
    assert car._conn.execute("SELECT odometer_km FROM charges").fetchone()["odometer_km"] is None


def test_the_backfill_never_stamps_a_zero(car):
    _charge(car, day=3, odo=None)
    _position(car, "2026-07-03T10:00:00+00:00", 0.0)
    _rerun_backfill(car)
    assert car._conn.execute("SELECT odometer_km FROM charges").fetchone()["odometer_km"] is None


def test_the_backfill_runs_once_and_leaves_a_typed_reading_alone(car):
    """It is guarded by a one-shot setting because `positions` holds hundreds of thousands of rows
    — and it only ever fills a hole, so a reading somebody typed is never overwritten."""
    _charge(car, day=3, odo=7000.0)
    _position(car, "2026-07-03T10:00:00+00:00", 4321.0)
    _rerun_backfill(car)
    assert car._conn.execute("SELECT odometer_km FROM charges").fetchone()["odometer_km"] == 7000.0
    assert car.get_setting("charges_odometer_backfill_v1") == "1"


# ── A · the euros start where the kilometres start ────────────────────────────

def test_nicos_shape_no_longer_divides_months_by_one_afternoon(car):
    """His archive, in miniature: charges going back weeks, one day of recorded driving. The old
    card put every euro over those 46 km."""
    for day in range(1, 11):
        _charge(car, day=day, kwh=30.0, cost=15.0)          # 10 charges, 150 €
    _trip(car, day=20, km=46.0)                             # …and one day of driving, later
    _charge(car, day=20, kwh=30.0, cost=15.0, hour=12)      # the only charge inside it
    out = db_reader.cost_per_100km()
    assert out["km"] == 46.0
    assert out["total_100km"] == round(15.0 * 100 / 46.0, 2), "one charge's euros, not ten"
    assert out["basis"] == "trips"


def test_a_charge_after_the_last_trip_keeps_its_money(car):
    """The right edge stays open on purpose: those kilometres arrive tomorrow, and dropping the
    charge would make the figure jitter every day for everybody — the same "a full tank raises it
    until you drive it" property already accepted on the petrol side."""
    _trip(car, day=10, km=100.0)
    _charge(car, day=10, kwh=20.0, cost=5.0)
    _charge(car, day=28, kwh=20.0, cost=5.0)                # after the last trip
    assert db_reader.cost_per_100km()["total_100km"] == 10.0


# ── E · which kilometres win ──────────────────────────────────────────────────

def test_an_ordinary_history_is_left_exactly_where_it_was(car):
    """The measurement that made this safe to ship: on Silvio's own B10 the trips price 119.74 €
    of 119.74 and the odometer 104.27, so the trips win and the card does not move at all."""
    for day in (10, 11, 12):
        _trip(car, day=day, km=100.0)
        _charge(car, day=day, kwh=20.0, cost=5.0, odo=1000.0 + day * 100)
    out = db_reader.cost_per_100km()
    assert out["basis"] == "trips"
    assert out["km"] == 300.0
    assert out["total_100km"] == 5.0


def test_the_odometer_wins_where_the_trips_cannot_speak(car):
    """@nico89612 after he fills the km in: the trips price one charge out of eleven, the odometer
    prices ten. It is not a threshold anybody chose — it is which basis covers more of the money."""
    for day in range(1, 11):
        _charge(car, day=day, kwh=30.0, cost=15.0, odo=10000.0 + day * 200)
    _trip(car, day=20, km=46.0)
    _charge(car, day=20, kwh=30.0, cost=15.0, hour=12, odo=12100.0)
    out = db_reader.cost_per_100km()
    assert out["basis"] == "odometer"
    assert out["km"] == 12100.0 - 10200.0, "first stamped reading to the last"
    # brim-to-brim: the closing charge's energy is still in the battery, so its euros are not billed
    assert out["total_100km"] == round(150.0 * 100 / 1900.0, 2)


def test_two_readings_that_do_not_move_are_not_a_measurement(car):
    """A mistyped odometer, or two charges at the same reading. Refused, not absorbed — dividing by
    zero kilometres is not a number and dividing by a negative one is worse.

    ⚠️ The readings sit BEFORE the only trip on purpose. With them inside it, the coverage
    comparison would send the card back to the trips on its own and the span guard could be deleted
    without a single test noticing — a mutation proved exactly that. Here the odometer basis prices
    four charges against the trips' one, so it wins on coverage and ONLY the guard can stop it.
    → [[feedback-a-green-test-can-assert-the-bug]]"""
    for n, odo in enumerate((5000.0, 4800.0, 4600.0, 4400.0), start=1):   # backwards
        _charge(car, day=n, kwh=20.0, cost=5.0, odo=odo)
    _trip(car, day=20, km=100.0)
    _charge(car, day=20, kwh=20.0, cost=5.0, hour=12)
    out = db_reader.cost_per_100km()
    assert out["basis"] == "trips", "a span that runs backwards is not a distance"
    assert out["km"] == 100.0


def test_a_car_with_no_trips_at_all_can_still_be_priced(car):
    """Silvio's own question: someone who kept a notebook and installs Mate in six months. There is
    no trip to divide by and there never will be — the odometer they type is the only distance
    those charges will ever have."""
    for day in range(1, 6):
        _charge(car, day=day, kwh=30.0, cost=12.0, odo=20000.0 + day * 400)
    out = db_reader.cost_per_100km()
    assert out["basis"] == "odometer"
    assert out["km"] == 1600.0
    assert out["total_100km"] == round(48.0 * 100 / 1600.0, 2)


def test_the_kwh_half_follows_the_same_window(car):
    """🔴 The defect this whole file is about was two halves of one card on two windows. Letting
    the divisor move without moving the balance with it would have re-created it, in a new place.
    → [[feedback-two-numbers-one-word]]"""
    for day in range(1, 6):
        _charge(car, day=day, kwh=30.0, cost=12.0, odo=20000.0 + day * 400)
    card = db_reader.cost_per_100km()
    assert card["basis"] == "odometer"
    # charged inside [first stamped, last stamped] = days 1..4 = 120 kWh; SoC 20 → 20 across the
    # window, so nothing stayed in the pack: 120 kWh over 1600 km.
    assert card["kwh_100km"] == 7.5


# ── C · the odometer through the CSV ──────────────────────────────────────────

def _parse(text):
    from datetime import timezone
    return charge_import.parse_charge_csv(text, tz=timezone.utc, today=__import__("datetime").date(2027, 1, 1))


def test_the_csv_carries_the_odometer_as_its_last_column():
    """The importer's own rule, written in its header map: new optional columns go at the END, so
    every file anybody already has keeps working."""
    rows, errors = _parse("date,energy_kwh,cost,type,start_soc,end_soc,end,odometer_km\n"
                          "2026-05-01 08:00,30,8.10,AC,,,,18450\n")
    assert errors == [] and rows[0]["odometer_km"] == 18450.0


def test_a_file_without_the_column_still_imports():
    rows, errors = _parse("date,energy_kwh\n2026-05-01 08:00,30\n")
    assert errors == [] and rows[0]["odometer_km"] is None


def test_the_column_is_found_by_name_however_it_is_spelled():
    rows, _ = _parse("date,energy_kwh,odometer\n2026-05-01 08:00,30,18450\n")
    assert rows[0]["odometer_km"] == 18450.0


def test_an_odometer_of_zero_is_rejected_not_stored():
    rows, errors = _parse("date,energy_kwh,odometer_km\n2026-05-01 08:00,30,0\n")
    assert rows == [] and "odometer_km" in errors[0]


def test_an_absurd_odometer_is_a_typo():
    rows, errors = _parse("date,energy_kwh,odometer_km\n2026-05-01 08:00,30,999999999\n")
    assert rows == [] and "odometer_km" in errors[0]


def test_the_template_hands_out_the_column_and_says_it_is_km():
    assert "odometer_km" in charge_import.TEMPLATE
    assert charge_import.TEMPLATE.rstrip().endswith("odometer_km")
    assert "never miles" in charge_import.TEMPLATE


# ── D · the import fills in instead of duplicating ────────────────────────────

def test_re_importing_the_same_file_does_not_double_the_archive(car):
    """🔴 It used to. Every clean line went straight to `add_manual_charge`, so 152 charges became
    304 and the money with them — and on a figure that was already wrong nobody would have seen it."""
    row = {"started_at": "2026-07-03T10:00:00+00:00", "ended_at": None, "energy_kwh": 30.0,
           "cost": 12.0, "charge_type": "AC", "start_soc": None, "end_soc": None,
           "odometer_km": None}
    assert db_reader.import_charge_row(row) == "added"
    assert db_reader.import_charge_row(row) == "unchanged"
    assert car._conn.execute("SELECT COUNT(*) c FROM charges").fetchone()["c"] == 1


def test_a_second_pass_carrying_the_odometer_fills_the_charge_in(car):
    """Which is the whole point: someone with a year of typed-in history adds the kilometres to it
    without deleting a single row first."""
    row = {"started_at": "2026-07-03T10:00:00+00:00", "ended_at": None, "energy_kwh": 30.0,
           "cost": 12.0, "charge_type": "AC", "start_soc": None, "end_soc": None,
           "odometer_km": None}
    db_reader.import_charge_row(row)
    assert db_reader.import_charge_row({**row, "odometer_km": 18450.0}) == "filled"
    got = car._conn.execute("SELECT COUNT(*) c, MAX(odometer_km) o FROM charges").fetchone()
    assert got["c"] == 1 and got["o"] == 18450.0


def test_a_charge_typed_into_the_form_keeps_its_odometer(car):
    """The other half of the same door: not the CSV, the "add a past charge" form. A mutation that
    dropped the column from that INSERT went unnoticed — every test here reached the odometer
    through the import path."""
    cid = db_reader.add_manual_charge("2026-07-03T10:00:00+00:00", 30.0, 12.0, "AC",
                                      odometer_km=18450.0)
    assert car._conn.execute(
        "SELECT odometer_km FROM charges WHERE id=?", (cid,)).fetchone()["odometer_km"] == 18450.0


def test_a_matched_row_rewrites_the_odometer_and_nothing_else(car):
    """⚠️ Declared and deliberately narrow. Mate may have computed the cost from a real charging
    curve; a re-import that quietly overwrote it would be a fresh way to lose data."""
    car.create_charge(1, _frame(soc=20.0, odo=None))
    car._conn.execute("UPDATE charges SET started_at='2026-07-03T10:00:00+00:00',"
                      " ended_at='2026-07-03T11:00:00+00:00', energy_added_kwh=30.0, cost=9.99,"
                      " charge_type='DC', odometer_km=NULL")
    car._conn.commit()
    db_reader.import_charge_row({"started_at": "2026-07-03T10:00:00+00:00", "ended_at": None,
                                 "energy_kwh": 30.0, "cost": 1.0, "charge_type": "AC",
                                 "start_soc": None, "end_soc": None, "odometer_km": 18450.0})
    row = car._conn.execute("SELECT cost, charge_type, odometer_km FROM charges").fetchone()
    assert row["odometer_km"] == 18450.0
    assert row["cost"] == 9.99 and row["charge_type"] == "DC", "the measured charge is untouched"


def test_a_different_session_at_the_same_minute_is_still_a_new_one(car):
    """The energy has to agree too: either test alone is a coincidence waiting to happen."""
    row = {"started_at": "2026-07-03T10:00:00+00:00", "ended_at": None, "energy_kwh": 30.0,
           "cost": 12.0, "charge_type": "AC", "start_soc": None, "end_soc": None,
           "odometer_km": None}
    db_reader.import_charge_row(row)
    assert db_reader.import_charge_row({**row, "energy_kwh": 8.0}) == "added"
    assert car._conn.execute("SELECT COUNT(*) c FROM charges").fetchone()["c"] == 2


# ── F · how far the car went between two charges ──────────────────────────────

def _cards(**filters):
    """The charges as the PAGE gets them. 🔴 Not `get_charges_grouped`: the Charges page never
    calls it. Every card the user sees comes from the calendar or from search, both through
    `_localized_charges` — and the first version of this feature attached the kilometres to the
    function nobody renders, with four green tests over it.
    → [[feedback-gate-a-feature-find-every-copy]]"""
    return db_reader.search_charges(**filters)


def test_the_gap_between_two_charges_is_their_two_readings(car):
    _charge(car, day=3, odo=1000.0)
    _charge(car, day=8, odo=1272.0)
    by_odo = {c["odometer_km"]: c for c in _cards()}
    assert by_odo[1272.0]["km_since_prev"] == 272.0
    assert "km_since_prev" not in by_odo[1000.0], "nothing came before the first one"


def test_the_pairing_reads_oldest_first(car):
    """⚠️ `get_charges` hands its rows back NEWEST-first. Pairing them in the order given
    subtracts the future from the past, every gap comes out negative, and the guard then hides all
    of them — the feature would vanish in complete silence rather than break."""
    for n, odo in enumerate((1000.0, 1100.0, 1250.0), start=3):
        _charge(car, day=n, odo=odo)
    assert sorted(c["km_since_prev"] for c in _cards() if c.get("km_since_prev")) == [100.0, 150.0]


def test_two_charges_the_same_afternoon_say_nothing(car):
    """Ten of these on the real B10 — plugged in twice with no driving in between. A "0 km since
    the last charge" line is noise, not information."""
    _charge(car, day=3, odo=1000.0, hour=9)
    _charge(car, day=3, odo=1000.0, hour=14)
    assert all(not c.get("km_since_prev") for c in _cards())


def test_a_reading_that_went_backwards_is_never_printed(car):
    """Somebody typed the trip's kilometres instead of the total. "-300 km since the last charge"
    would be worse than nothing."""
    _charge(car, day=3, odo=1000.0)
    _charge(car, day=8, odo=700.0)
    assert all(not c.get("km_since_prev") for c in _cards())


def test_a_filtered_view_does_not_pair_two_strangers(car):
    """🔴 The Charges page shows ONE DAY at a time and search shows whatever matched. Pairing
    inside the list being rendered would subtract two charges that are not neighbours and print
    the gap across everything filtered out — confidently, and wrong. The figure is computed over
    the whole history and looked up by id."""
    _charge(car, day=3, odo=1000.0)
    _charge(car, day=5, odo=1100.0)
    _charge(car, day=8, odo=1250.0)
    only_last = _cards(date_from="2026-07-08", date_to="2026-07-08")
    assert len(only_last) == 1
    assert only_last[0]["km_since_prev"] == 150.0, "its real neighbour, not the one still on screen"


# ── the page still renders it ─────────────────────────────────────────────────

def test_every_language_carries_the_new_strings():
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    keys = ("stats_cost100_note_odo", "manual_charge_odometer", "import_filled",
            "import_all_known", "charge_km_since_prev", "charge_km_since_prev_hint")
    for f in sorted(root.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))["translations"]
        assert not [k for k in keys if k not in d], f"{f.name} is missing strings"


def test_the_note_names_whose_kilometres_they_are():
    """Two different distances under one sentence is the defect Silvio named. The odometer's
    kilometres are usually MORE than Mate recorded — 4.4% more over ten weeks on a real B10 — so
    calling them "recorded" would be the same quiet lie the page's own header exists to stop."""
    import pathlib
    stats = (pathlib.Path(__file__).resolve().parent.parent
             / "web" / "templates" / "statistics.html").read_text()
    assert "stats_cost100_note_odo' if c100.basis == 'odometer'" in stats


def test_a_database_without_the_odometer_column_still_answers(car):
    """The migration lives in the poller and the web never alters the database, so between an
    update and the poller's next start the column is simply absent — a 500 on the Statistics page
    is what that cost in v3.6.6."""
    car._conn.execute("ALTER TABLE charges RENAME COLUMN odometer_km TO odometer_km_x")
    car._conn.commit()
    _trip(car, day=10, km=100.0)
    car._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
                      " energy_added_kwh, cost) VALUES (1,'2026-07-10T10:00:00+00:00',"
                      "'2026-07-10T11:00:00+00:00',20,60,20.0,5.0)")
    car._conn.commit()
    out = db_reader.cost_per_100km()
    assert out["basis"] == "trips" and out["total_100km"] == 5.0
    assert db_reader.charges_have_odometer() is False


def test_the_charges_page_hides_the_field_it_cannot_store(car):
    """v3.6.6, on Silvio's own instance: a field that silently drops what you type into it is worse
    than no field at all."""
    import pathlib
    page = (pathlib.Path(__file__).resolve().parent.parent
            / "web" / "templates" / "charges.html").read_text()
    assert "{% if charges_have_odometer %}" in page
    assert 'name="odometer"' in page
    main = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()
    assert "charges_have_odometer=db_reader.charges_have_odometer()" in main
    assert "units.dist_to_km(odo)" in main, "typed in the reader's unit, stored in km"
