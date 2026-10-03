"""One plug-in is one charge, even when the car reported it in pieces — #374, @Andreexylus.

THE CAR'S PART. It declares the cable GONE the instant the current stops: `plug_connected` 1→0 and
`charging` 1→0 in the same frame, current −11.8 A → 0.5 A, remaining time → NULL, and everything
back a minute later. Measured on a B10 the night of 29→30 July 2026 — cable in at 19:30, out the
next morning, untouched in between — one plug-in recorded as six charges, with pauses of 60, 70,
180 and 60 s. @Andreexylus's bundle shows eleven such pauses over six nights, 30 to 60 s each. It
is what a load-balancing wallbox, a solar-surplus charger or a utility pacing the load looks like
from inside the car, and nothing in the frame distinguishes it from an unplug.

WHAT THIS CHANGES, AND WHAT IT DOES NOT. The poller still closes the charge on that frame: the
state machine is untouched, and so is the rule that a closed charge is never recomputed. What
changes is that the pieces no longer wait to be joined by hand. The join is `merge_charges` itself
— every guard it has — called with a six-minute window instead of the thirty the manual button
allows: six minutes is the worst pause ever measured here, rounded up, while thirty is where the
call belongs to the person who was there. The pieces are joined, never rewritten, so **Split** puts
them back exactly as the car reported them.

WHY A CURSOR. Each charge is examined once, in id order, behind `charges_pause_join_cursor` — the
device the events derivation uses. That is what makes an automatic join safe: a pair the owner
splits is never looked at again, so this can never undo a human decision. At 0 on the first run it
walks the history, and the nights already in the database come back together too.
"""
from datetime import datetime, timedelta, timezone

import db_reader
from events_fixture import Car, web

NOW = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)


def charge(car, start, minutes, soc_from, soc_to, *, ended=True, **cols) -> int:
    """One charge row as the poller writes it, starting `start` and lasting `minutes`."""
    end = (start + timedelta(minutes=minutes)).isoformat() if ended else None
    keys = "".join(f", {k}" for k in cols)
    marks = "".join(", ?" for _ in cols)
    return int(car.db._conn.execute(
        f"INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        f" energy_added_kwh, location_type{keys}) VALUES (?, ?, ?, ?, ?, ?, 'HOME'{marks})",
        (car.vid, start.isoformat(), end, soc_from, soc_to,
         round((soc_to - soc_from) / 100 * 65, 2), *cols.values())).lastrowid or 0)


def parents(car):
    """Every charge, with the row it was joined into (None when it stands on its own)."""
    return {r["id"]: r["merged_into_id"] for r in car.db._conn.execute(
        "SELECT id, merged_into_id FROM charges ORDER BY id")}


def a_car(tmp_path, monkeypatch):
    car = Car(tmp_path, name="charges.db")
    web(car, monkeypatch)
    return car


# ── the pause ────────────────────────────────────────────────────────────────

def test_a_minute_of_pause_is_one_charge(tmp_path, monkeypatch):
    """The shortest pause measured: the cable reads gone, and is back sixty seconds later."""
    car = a_car(tmp_path, monkeypatch)
    first = charge(car, NOW, 90, 40.0, 60.0)
    second = charge(car, NOW + timedelta(minutes=91), 90, 60.0, 80.0)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 1
    assert parents(car) == {first: None, second: first}


def test_the_night_that_came_back_as_six_rows_is_one_charge(tmp_path, monkeypatch):
    """29→30 July: one plug-in, five pauses of 60 to 180 s, six rows. Every piece joins the first —
    a chain, because each row's neighbour is already joined by the time its turn comes."""
    car = a_car(tmp_path, monkeypatch)
    at, soc, ids = NOW, 30.0, []
    for pause in (0, 60, 70, 180, 60, 60):
        at += timedelta(seconds=pause)
        ids.append(charge(car, at, 55, soc, soc + 10.0))
        at, soc = at + timedelta(minutes=55), soc + 10.0
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 5
    assert parents(car) == {ids[0]: None, **{i: ids[0] for i in ids[1:]}}


def test_the_longest_pause_measured_still_joins(tmp_path, monkeypatch):
    """334 s — the worst in the 35 charges of the reference database. Six minutes was chosen to
    cover it, so a window that refused it would be the wrong window."""
    car = a_car(tmp_path, monkeypatch)
    first = charge(car, NOW, 60, 40.0, 55.0)
    second = charge(car, NOW + timedelta(minutes=60, seconds=334), 60, 55.0, 70.0)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 1
    assert parents(car)[second] == first


# ── what is not a pause ──────────────────────────────────────────────────────

def test_two_charges_an_hour_apart_stay_two(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    first = charge(car, NOW, 60, 40.0, 55.0)
    second = charge(car, NOW + timedelta(minutes=120), 60, 55.0, 70.0)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 0
    assert parents(car) == {first: None, second: None}


def test_a_gap_past_the_window_is_left_to_the_owner(tmp_path, monkeypatch):
    """Seven minutes: inside the thirty the manual join allows, outside the six taken without
    asking. The button is still there, and the charge is still joinable by hand."""
    car = a_car(tmp_path, monkeypatch)
    first = charge(car, NOW, 60, 40.0, 55.0)
    second = charge(car, NOW + timedelta(minutes=67), 60, 55.0, 70.0)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 0
    assert db_reader.merge_charges(first, second)["ok"] is True
    assert parents(car)[second] == first


def test_a_soc_that_fell_in_the_gap_is_a_car_that_went_somewhere(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    charge(car, NOW, 60, 40.0, 70.0)
    second = charge(car, NOW + timedelta(minutes=63), 60, 61.0, 80.0)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 0
    assert parents(car)[second] is None


def test_a_drive_in_the_gap_separates_the_two_charges(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    charge(car, NOW, 60, 40.0, 70.0)
    gap = NOW + timedelta(minutes=60)
    car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km) VALUES (?, ?, ?, 4.0)",
        (car.vid, (gap + timedelta(seconds=30)).isoformat(),
         (gap + timedelta(minutes=2)).isoformat()))
    second = charge(car, gap + timedelta(minutes=3), 60, 70.0, 80.0)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 0
    assert parents(car)[second] is None


def test_a_different_charging_place_is_a_different_charge(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    charge(car, NOW, 60, 40.0, 55.0, charging_place_id=1)
    second = charge(car, NOW + timedelta(minutes=62), 60, 55.0, 70.0, charging_place_id=2)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 0
    assert parents(car)[second] is None


# ── the cursor ───────────────────────────────────────────────────────────────

def test_a_charge_the_owner_split_is_never_joined_again(tmp_path, monkeypatch):
    """The whole reason the cursor exists. Split what was joined, run again: it stays split."""
    car = a_car(tmp_path, monkeypatch)
    first = charge(car, NOW, 90, 40.0, 60.0)
    second = charge(car, NOW + timedelta(minutes=91), 90, 60.0, 80.0)
    car.db._conn.commit()
    db_reader.join_charges_split_by_a_pause()
    db_reader.unmerge_charges(first)
    assert parents(car)[second] is None
    assert db_reader.join_charges_split_by_a_pause() == 0
    assert parents(car)[second] is None


def test_running_twice_joins_nothing_twice(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    charge(car, NOW, 90, 40.0, 60.0)
    charge(car, NOW + timedelta(minutes=91), 90, 60.0, 80.0)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 1
    assert db_reader.join_charges_split_by_a_pause() == 0


def test_a_charge_still_running_is_waited_for_not_stepped_over(tmp_path, monkeypatch):
    """The cursor stops before an open charge. Otherwise the piece that follows it would be
    examined while its own neighbour had no end yet, and then never again."""
    car = a_car(tmp_path, monkeypatch)
    open_id = charge(car, NOW, 90, 40.0, 60.0, ended=False)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 0
    assert db_reader.get_setting("charges_pause_join_cursor", "0") in ("", "0")

    car.db._conn.execute("UPDATE charges SET ended_at = ? WHERE id = ?",
                         ((NOW + timedelta(minutes=90)).isoformat(), open_id))
    second = charge(car, NOW + timedelta(minutes=92), 90, 60.0, 80.0)
    car.db._conn.commit()
    assert db_reader.join_charges_split_by_a_pause() == 1
    assert parents(car)[second] == open_id


def test_the_window_is_the_measurement_not_the_manual_one():
    """The two numbers answer different questions, and must not drift into each other."""
    assert db_reader.CHARGE_PAUSE_JOIN_GAP == 6
    assert db_reader.CHARGE_MERGE_GAP_DEFAULT == 30
    assert db_reader.CHARGE_PAUSE_JOIN_GAP < db_reader.CHARGE_MERGE_GAP_DEFAULT
