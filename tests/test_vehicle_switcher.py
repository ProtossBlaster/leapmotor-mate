"""The sidebar car switcher: which car every scoped read resolves to.

One setting (`active_vehicle_vin`) moves trips, charges, map, stats, the model badge and the
ability-gated nav together, because they all resolve through `_current_vehicle_id()`. The
properties that matter are the ones that keep a single-car install — i.e. everyone, today —
byte-identical, and that stop a bad or stale choice from stranding the UI on a car that isn't
there.
"""
import db as D
import db_reader


def _db(tmp_path, monkeypatch, *cars):
    path = str(tmp_path / "sw.db")
    db = D.Database(path)
    for i, (vin, car_type) in enumerate(cars, start=1):
        db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (?,?,?)",
                         (i, vin, car_type))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db


def test_defaults_to_the_first_car_when_nothing_is_chosen(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    assert db_reader._current_vehicle_id() == 1


def test_the_choice_moves_every_scoped_read(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    assert db_reader.set_active_vehicle("VIN_TWO") is True
    assert db_reader._current_vehicle_id() == 2
    v, _ = db_reader.get_vehicle()
    assert v["car_type"] == "T03"          # the model badge + per-model gating follow it


def test_a_stale_choice_falls_back_instead_of_stranding_the_ui(tmp_path, monkeypatch):
    """The named car was removed (re-setup, account change). Nothing should blank: the ORDER BY
    scores every row equally and the tiebreak on id hands back the first car, as before."""
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "VIN_LONG_GONE")
    assert db_reader._current_vehicle_id() == 1
    v, _ = db_reader.get_vehicle()
    assert v is not None and v["id"] == 1


def test_an_unknown_vin_is_refused_rather_than_stored(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    assert db_reader.set_active_vehicle("NOT_A_CAR") is False
    assert db_reader.get_setting(db_reader.ACTIVE_VEHICLE_SETTING, "") == ""
    assert db_reader._current_vehicle_id() == 1


def test_get_vehicles_lists_them_oldest_first(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    assert [v["car_type"] for v in db_reader.get_vehicles()] == ["B10", "T03"]


def test_single_car_is_untouched(tmp_path, monkeypatch):
    """One car → the switcher isn't rendered (base.html gates on len > 1) and the resolution is
    the same id with or without a choice stored. No install in the field can notice this feature."""
    _db(tmp_path, monkeypatch, ("VIN_ONLY", "B10"))
    assert len(db_reader.get_vehicles()) == 1
    assert db_reader._current_vehicle_id() == 1
    db_reader.set_active_vehicle("VIN_ONLY")
    assert db_reader._current_vehicle_id() == 1


def test_no_vehicle_yet_still_resolves_to_none(tmp_path, monkeypatch):
    """Fresh install: None keeps `COALESCE(?, vehicle_id)` matching every row rather than
    filtering the UI to empty."""
    _db(tmp_path, monkeypatch)
    assert db_reader._current_vehicle_id() is None
    assert db_reader.get_vehicles() == []
