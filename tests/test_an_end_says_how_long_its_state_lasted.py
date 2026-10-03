"""A state's beginning and its end are two rows, and the end says how long the state lasted.

The start of an end is the previous row of its kind in the order the poller wrote them, found in the
car's whole history, so an end whose start is before the days shown still says when it began. Beside
the length, an end answers the question its own row raises — and only that one: how far READY drove
and what it used, what the climate did to the cabin, whether the cable charged and how long it waited
for its schedule (until energy flowed: a 0 kWh session is the charger still holding it); a tailgate
closing says how long it was open and nothing more. A climate start says
whether the car was parked or on a trip, from every stored trip piece whatever the view's filters (the
pause inside a merged trip is time parked). A state still on says so only with a fresh frame.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db_reader
from events_fixture import VIN, Car, event_row, grouped, row_text, rows, web

ZONE = ZoneInfo("Europe/Warsaw")
DAY = datetime.now(ZONE).date() - timedelta(days=1)
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _local(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


def _on(day=DAY, **query):
    return rows(grouped(date_from=day.isoformat(), date_to=day.isoformat(), **query))


def _by_kind(listed, kind, on):
    (row,) = [r for r in listed if r["kind"] == kind and r["on"] is on]
    return row


def _trip(car, start, end, parent=None):
    cur = car.db._conn.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, merged_into_id) VALUES (?, ?, ?, ?)",
                               (car.vid, start.isoformat(), end.isoformat() if end else None, parent))
    car.db._conn.commit()
    return cur.lastrowid


def _charge(car, start, end, kwh):
    car.db._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh) VALUES (?, ?, ?, ?)",
                         (car.vid, start.isoformat(), end.isoformat(), kwh))
    car.db._conn.commit()


def test_every_end_says_how_long_and_when_it_began(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    event_row(car, "unlocked", _local(DAY, 14, 18))
    event_row(car, "unlocked", _local(DAY, 14, 21), state=0)
    locked, unlocked = _on()
    assert (locked["label"], locked["duration_min"], locked["from_hms"]) == ("Locked", 3, "14:18:00")
    assert locked["from_listed"] and locked["from_anchor"] == unlocked["anchor"]
    assert (unlocked["label"], unlocked["duration_min"], unlocked["from_anchor"]) == ("Unlocked", None, None)


def test_ready_says_how_far_and_what_it_used_and_a_tailgate_does_not(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang="pl")
    event_row(car, "ready", _local(DAY, 13, 50), soc=81.6, odometer_km=12000.0)
    event_row(car, "ready", _local(DAY, 14, 19), state=0, soc=78.1, odometer_km=12013.0)
    event_row(car, "trunk", _local(DAY, 14, 19), soc=78.1, odometer_km=12013.0)
    event_row(car, "trunk", _local(DAY, 14, 20), state=0, soc=78.1, odometer_km=12013.0)
    listed = _on()
    ready, trunk = _by_kind(listed, "ready", False), _by_kind(listed, "trunk", False)
    assert (ready["distance_km"], ready["soc_from"], ready["soc_to"], ready["duration_min"]) == (13.0, 81.6, 78.1, 29)
    assert (trunk["distance_km"], trunk["soc_from"], trunk["duration_min"]) == (None, None, 1)
    html = client.get(f"/events?date_from={DAY}&date_to={DAY}").text
    assert "READY wyłączone" in html and "13 km" in html and "81,6% → 78,1%" in html


def test_the_climate_says_what_it_did_in_the_readers_language(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang="pl")
    event_row(car, "climate", _local(DAY, 7, 40), inside_temp=17.0, climate_target_temp=21.0, outside_temp=8.0)
    event_row(car, "climate", _local(DAY, 7, 55), state=0, inside_temp=21.5, climate_target_temp=21.0, outside_temp=8.0)
    listed = _on()
    on, off = _by_kind(listed, "climate", True), _by_kind(listed, "climate", False)
    assert (on["ctx"], on["target_temp"], on["outside_temp"]) == ("parked", 21.0, 8.0)
    assert (off["cabin_from"], off["cabin_to"], off["duration_min"]) == (17.0, 21.5, 15)
    html = client.get(f"/events?date_from={DAY}&date_to={DAY}").text
    assert row_text(html, on["anchor"]) == "Klimatyzacja włączona · na postoju · cel 21 °C · Na zewnątrz 8 °C"
    assert row_text(html, off["anchor"]) == "Klimatyzacja wyłączona · po 15 min · Kabina 17 → 21,5 °C"


def test_parked_or_on_a_trip_comes_from_the_trip_pieces_whatever_the_filters(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    parent = _trip(car, _local(DAY, 9), _local(DAY, 9, 30))
    _trip(car, _local(DAY, 10), _local(DAY, 10, 40), parent=parent)
    event_row(car, "climate", _local(DAY, 9, 45))                    # in the pause of the merged trip
    event_row(car, "climate", _local(DAY, 9, 50), state=0)
    event_row(car, "climate", _local(DAY, 10, 20))                   # in its second piece
    event_row(car, "climate", _local(DAY, 10, 30), state=0)
    for query in ({}, {"f": "1", "group": ["climate"]}):
        starts = [r["ctx"] for r in _on(**query) if r["kind"] == "climate" and r["on"]]
        assert starts == ["during_trip", "parked"], query


def test_the_cable_says_what_it_charged_and_how_long_it_waited(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "cable", _local(DAY, 8), soc=30.0)                 # charging at once
    _charge(car, _local(DAY, 8, 1), _local(DAY, 9), 6.0)
    event_row(car, "cable", _local(DAY, 9, 5), state=0, soc=40.0)
    event_row(car, "cable", _local(DAY, 19, 30), soc=40.0)            # waiting for the night tariff
    _charge(car, _local(DAY, 20, 30), _local(DAY, 21), 0.0)           # a session with nothing flowing
    _charge(car, _local(DAY, 23, 25), _local(DAY, 23, 40), 5.0)       # one plug-in, stored in two pieces
    _charge(car, _local(DAY, 23, 45), _local(DAY, 23, 58), 3.3)
    event_row(car, "cable", _local(DAY, 23, 59), state=0, soc=53.0)
    listed = [r for r in _on(f="1", group=["charging"]) if r["kind"] == "cable" and not r["on"]]
    evening, morning = listed
    assert (evening["energy_kwh"], evening["soc_from"], evening["soc_to"]) == (8.3, 40.0, 53.0)
    assert round(evening["waited_min"]) == 235
    assert (morning["energy_kwh"], morning["waited_min"]) == (6.0, None)
    html = client.get(f"/events?date_from={DAY}&date_to={DAY}").text
    assert "8.3 kWh · " in row_text(html, evening["anchor"]) and " · charging began after 3h 55m" in row_text(html, evening["anchor"])


def test_a_start_before_the_days_shown_is_found(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    event_row(car, "cable", _local(DAY - timedelta(days=12), 18, 5))
    event_row(car, "cable", _local(DAY, 7), state=0)
    (end,) = _on()
    assert (end["from_hms"], end["from_listed"]) == ("18:05:00", False)
    assert end["from_day"] and end["duration_min"] == (_local(DAY, 7) - _local(DAY - timedelta(days=12), 18, 5)).total_seconds() / 60


def test_pairs_follow_the_write_order_when_the_clock_steps_back_over_midnight(tmp_path, monkeypatch):
    """The host clock stepped back across midnight: the first pair's end is dated the day before,
    so a reading by time would skip it and close the first start with the second pair's end."""
    car = Car(tmp_path)
    web(car, monkeypatch)
    today = DAY + timedelta(days=1)
    event_row(car, "unlocked", _local(today, 0, 5))
    event_row(car, "unlocked", _local(DAY, 23, 58), state=0)
    event_row(car, "unlocked", _local(today, 0, 10))
    event_row(car, "unlocked", _local(today, 0, 20), state=0)
    listed = _on(today)
    assert [(r["id"], r["on"]) for r in listed] == [(4, False), (3, True), (1, True)]
    assert listed[0]["from_anchor"] == "ev-signal-3" and listed[0]["duration_min"] == 10
    assert not listed[2]["open"], "its end is the row written after it, a day earlier on the clock"
    (skewed,) = _on()
    assert (skewed["id"], skewed["from_anchor"], skewed["duration_min"]) == (2, "ev-signal-1", None)


def _ms(at):
    return int(at.timestamp() * 1000)


def test_the_cars_clock_times_a_row_and_its_length(tmp_path, monkeypatch):
    """A row is timed by the frame that showed it, not by when Mate wrote it down: a frame delivered
    late does not lengthen the state. A row without the car's clock falls back to Mate's."""
    car = Car(tmp_path)
    web(car, monkeypatch)
    opened = _local(DAY, 14, 18)
    event_row(car, "trunk", opened + timedelta(seconds=2), frame_ts=_ms(opened))
    event_row(car, "trunk", opened + timedelta(seconds=40), state=0, frame_ts=_ms(opened + timedelta(seconds=35)))
    event_row(car, "unlocked", _local(DAY, 15) + timedelta(seconds=9))
    listed = _on()
    unlocked, closed, opened_row = listed
    assert (closed["hms"], round(closed["duration_min"] * 60), opened_row["hms"]) == ("14:18:35", 35, "14:18:00")
    assert unlocked["hms"] == "15:00:09"


def test_a_frame_written_after_midnight_stays_on_its_own_day(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    frame = _local(DAY, 23, 59) + timedelta(seconds=50)
    event_row(car, "cable", _local(DAY + timedelta(days=1), 2, 10), frame_ts=_ms(frame))
    (row,) = _on()
    assert (row["day"], row["hms"]) == (DAY, "23:59:50")
    assert _on(DAY + timedelta(days=1)) == [], "the next day's window does not take it by Mate's clock"


def test_a_climate_start_read_with_a_trips_start_is_during_the_trip(tmp_path, monkeypatch):
    """The trip starts on Mate's clock, so the climate is matched on Mate's clock too: its frame is
    two seconds older than the reading that started the trip."""
    car = Car(tmp_path)
    web(car, monkeypatch)
    reading = _local(DAY, 10, 0) + timedelta(seconds=2)
    _trip(car, reading, _local(DAY, 10, 30))
    event_row(car, "climate", reading, frame_ts=_ms(reading - timedelta(seconds=2)))
    (row,) = [r for r in _on() if r["kind"] == "climate"]
    assert (row["hms"], row["ctx"]) == ("10:00:00", "during_trip")


def _position(car, frame_age_s, recorded_age_s=10):
    """The car's last frame, `frame_age_s` old now, not when the module was imported (a long suite
    would age it), read by the detector as the poller's round would. Returns the frame's time."""
    now = datetime.now(timezone.utc)
    frame = now - timedelta(seconds=frame_age_s)
    car.db._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, frame_ts) VALUES (?, ?, ?)",
                         (car.vid, (now - timedelta(seconds=recorded_age_s)).isoformat(), int(frame.timestamp() * 1000)))
    car.db._conn.commit()
    car.derive()
    return frame


def test_a_state_still_on_says_so_on_a_fresh_frame_and_names_a_stale_one(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "cable", NOW - timedelta(hours=2))
    _position(car, frame_age_s=20)
    (row,) = rows(grouped())
    assert row["open"] and row["still_open"] and row["last_frame_hhmm"] is None
    html = client.get("/events").text
    assert row_text(html, row["anchor"]) == "Cable connected (in progress)", "its state right after its name"
    stale = _position(car, frame_age_s=1800, recorded_age_s=5)
    (row,) = rows(grouped())
    assert not row["still_open"]
    assert row["last_frame_hhmm"] == stale.astimezone(db_reader._local_tz()).strftime("%H:%M")


def test_the_four_sources_sort_on_one_clock(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    t0 = NOW - timedelta(hours=5)
    event_row(car, "unlocked", t0 + timedelta(minutes=10))
    car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min) VALUES (?, ?, ?, 12.0, 20)",
        (car.vid, (t0 + timedelta(minutes=20)).isoformat(), (t0 + timedelta(minutes=40)).isoformat()))
    car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh) VALUES (?, ?, ?, 8.0)",
        (car.vid, (t0 + timedelta(minutes=50)).isoformat(), (t0 + timedelta(minutes=90)).isoformat()))
    car.db._conn.commit()
    db_reader.log_command("lock", "confirmed", 1800, vin=VIN)
    listed = rows(grouped())
    assert [(r["source"], r["on"]) for r in listed] == [
        ("command", None), ("charge", False), ("charge", True), ("trip", False), ("trip", True), ("signal", True)]
    assert listed[0]["extra"] == "Lock · confirmed"


def test_a_command_is_named_in_the_readers_language(tmp_path, monkeypatch):
    """The log keeps the name of the function that sent a command; the row names the command, and
    one the locale does not know says only how it went."""
    car = Car(tmp_path)
    web(car, monkeypatch, lang="pl")
    db_reader.log_command("steering_heat_on", "confirmed", 900, vin=VIN)
    db_reader.log_command("_send_ac_on", "confirmed", 900, vin=VIN)           # the fan or the recirculation
    db_reader.log_command("a_helper_of_tomorrow", "timeout_car", None, vin=VIN)
    assert sorted(r["extra"] for r in rows(grouped("pl"))) == [
        "Włącz ogrzewanie kierownicy · potwierdzone", "Zmień ustawienia klimatyzacji · potwierdzone",
        "brak odpowiedzi auta"]


def test_another_cars_events_are_not_this_cars(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    other = car.db.ensure_vehicle("LFZOTHER000000002", "C10")
    car.db._conn.execute("INSERT INTO events (vehicle_id, kind, at, state) VALUES (?, 'unlocked', ?, 1)",
                         (other, (NOW - timedelta(hours=1)).isoformat()))
    car.db._conn.commit()
    event_row(car, "trunk", NOW - timedelta(hours=2))
    db_reader.set_active_vehicle(VIN)
    assert [r["kind"] for r in rows(grouped())] == ["trunk"]
    db_reader.set_active_vehicle("LFZOTHER000000002")
    assert [r["kind"] for r in rows(grouped())] == ["unlocked"]
