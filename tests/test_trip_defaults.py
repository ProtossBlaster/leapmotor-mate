"""#119: app-level trip defaults. Drive mode and One-Pedal are never reported by the cloud
(verified on-car), so they can only be tagged by hand. Two settings — default_drive_mode /
default_one_pedal — let a fixed-habit driver pre-fill every NEW trip instead of re-tagging each
one. Unset (or an invalid value) keeps the historical NULL / "not set". Existing trips are never
touched: the defaults are applied only at trip-creation time.

Runs entirely on a tmp_path DB (poller schema) — CI-safe, no settings DB."""
import types

import db as D


def _data(soc=80.0, odo=1000.0, lat=45.0, lon=9.0):
    return types.SimpleNamespace(soc=soc, odometer_km=odo, latitude=lat, longitude=lon)


def _trip_row(db, tid):
    return db._conn.execute("SELECT drive_mode, one_pedal FROM trips WHERE id=?", (tid,)).fetchone()


def test_unset_leaves_trip_not_set(tmp_path):
    """No defaults configured → a new trip is born NULL/NULL, exactly as before the feature."""
    db = D.Database(str(tmp_path / "t.db"))
    row = _trip_row(db, db.create_trip(1, _data()))
    assert row["drive_mode"] is None
    assert row["one_pedal"] is None


def test_defaults_prefill_new_trip(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_setting("default_drive_mode", "comfort")
    db.set_setting("default_one_pedal", "1")
    row = _trip_row(db, db.create_trip(1, _data()))
    assert row["drive_mode"] == "comfort"
    assert row["one_pedal"] == 1


def test_one_pedal_off_is_distinct_from_unset(tmp_path):
    """One-Pedal 'Off' (0) is a real choice, not the same as 'not set' (NULL) — it must persist as 0."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_setting("default_one_pedal", "0")
    row = _trip_row(db, db.create_trip(1, _data()))
    assert row["one_pedal"] == 0


def test_only_one_default_set(tmp_path):
    """Setting just one default leaves the other 'not set'."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_setting("default_drive_mode", "sport")
    row = _trip_row(db, db.create_trip(1, _data()))
    assert row["drive_mode"] == "sport"
    assert row["one_pedal"] is None


def test_invalid_setting_value_is_ignored(tmp_path):
    """A stray/garbage setting value can never land an invalid tag on a trip — it falls back to NULL.

    This test used to use "eco" as its example of "not a real drive mode", and that is how #185 got
    through: v2.13.0 added ECO and Custom to the list the WEB accepts and left the poller's copy at
    comfort/normal/sport, so a green test went on asserting that the poller was right to throw ECO
    away. The example is now a value that will never be a drive mode; the two real lists are pinned
    against each other in test_drive_modes_agree.py instead."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_setting("default_drive_mode", "ludicrous")   # not a drive mode, and never will be
    db.set_setting("default_one_pedal", "yes")          # not 0/1
    row = _trip_row(db, db.create_trip(1, _data()))
    assert row["drive_mode"] is None
    assert row["one_pedal"] is None


def test_drive_mode_case_insensitive(tmp_path):
    """The saver lower-cases, but be defensive if a raw value slips in some other way."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_setting("default_drive_mode", "Comfort")
    row = _trip_row(db, db.create_trip(1, _data()))
    assert row["drive_mode"] == "comfort"


def test_reconstructed_trip_also_gets_defaults(tmp_path):
    """A trip reconstructed from an odometer jump (#118) is still the user's drive — it must be
    born with the same defaults as a live trip."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(60.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    db.set_setting("default_drive_mode", "comfort")
    db.set_setting("default_one_pedal", "1")
    tid = db.create_reconstructed_trip(vid, 60.0, 1000.0, "2026-07-04T20:00:00+00:00", _data(soc=53.0, odo=1015.0))
    row = _trip_row(db, tid)
    assert row["drive_mode"] == "comfort"
    assert row["one_pedal"] == 1
