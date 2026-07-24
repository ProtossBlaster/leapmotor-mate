"""The web-side position write must keep the longitude sign — GitHub #158.

The poller resolves the signed/unsigned coordinate pair correctly (test_gps_sign.py, #30/#43),
but db_reader kept its OWN copy of that parse for the write that follows a command or the
Refresh button — and that copy read only the unsigned magnitude. Andreexylus' Lisbon B10 was
stored correctly by the poller and then, on the next Refresh, the NEWEST position row was filed
at +9.14 instead of -9.14: the sea off Sardinia, which is exactly what his Overview map showed.

Coordinates below are Lisbon (west of Greenwich, the broken case) and Milan (east, the case that
must not move). Latitude is never affected — both cars are north of the equator.
"""
import db as D
import db_reader


LISBON_SIGNED = {"2": -9.139337, "3": 38.722252, "3724": 9.139337, "3725": 38.722252,
                 "1010": 0, "1319": 0.0, "100003": 52.6}
MILAN_SIGNED = {"2": 9.124942, "3": 45.443407, "3724": 9.124942, "3725": 45.443407,
                "1010": 0, "1319": 0.0, "100003": 52.6}


def _db(tmp_path, monkeypatch, *, lon_sign=None, lat_sign=None, name="gps"):
    path = str(tmp_path / f"{name}.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN','B10')")
    for key, val in (("gps_lat_sign", lat_sign), ("gps_lon_sign", lon_sign)):
        if val is not None:
            db._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                             (key, str(val)))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _stored(path):
    import sqlite3
    con = sqlite3.connect(path)
    row = con.execute("SELECT latitude, longitude FROM positions ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return row


def test_signed_pair_is_preferred_over_the_unsigned_one(tmp_path, monkeypatch):
    """The real #158 case: both pairs arrive, only the signed one is right."""
    path = _db(tmp_path, monkeypatch, name="a")
    db_reader.save_fresh_signals(dict(LISBON_SIGNED))
    assert _stored(path) == (38.722252, -9.139337)      # NOT +9.14 (sea off Sardinia)


def test_unsigned_only_poll_uses_the_sign_the_poller_learned(tmp_path, monkeypatch):
    """Signals 2/3 missing from this poll → re-apply the persisted sign (#43), as the poller does."""
    path = _db(tmp_path, monkeypatch, lat_sign=1.0, lon_sign=-1.0, name="b")
    sig = {k: v for k, v in LISBON_SIGNED.items() if k not in ("2", "3")}
    db_reader.save_fresh_signals(sig)
    assert _stored(path) == (38.722252, -9.139337)


def test_unsigned_only_with_no_memory_is_left_alone(tmp_path, monkeypatch):
    """Nothing known about the hemisphere → store the magnitude, exactly as before this fix.
    A fresh install can't invent a sign; the poller fills the setting on its first signed poll."""
    path = _db(tmp_path, monkeypatch, name="c")
    sig = {k: v for k, v in LISBON_SIGNED.items() if k not in ("2", "3")}
    db_reader.save_fresh_signals(sig)
    assert _stored(path) == (38.722252, 9.139337)


def test_east_of_greenwich_car_is_untouched(tmp_path, monkeypatch):
    """Milan: signed and unsigned agree — the fix must be a no-op for every eastern car."""
    path = _db(tmp_path, monkeypatch, name="d")
    db_reader.save_fresh_signals(dict(MILAN_SIGNED))
    assert _stored(path) == (45.443407, 9.124942)


def test_east_car_with_positive_memory_stays_positive(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch, lat_sign=1.0, lon_sign=1.0, name="e")
    sig = {k: v for k, v in MILAN_SIGNED.items() if k not in ("2", "3")}
    db_reader.save_fresh_signals(sig)
    assert _stored(path) == (45.443407, 9.124942)


def test_fallback_chain_reaches_2190_2191(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch, lat_sign=1.0, lon_sign=-1.0, name="f")
    db_reader.save_fresh_signals({"2190": 38.722252, "2191": 9.139337,
                                  "1010": 0, "1319": 0.0, "100003": 52.6})
    assert _stored(path) == (38.722252, -9.139337)


def test_a_car_with_no_fix_stores_zero_not_a_signed_zero(tmp_path, monkeypatch):
    """No GPS in the poll at all → 0.0, never a sign applied to nothing."""
    path = _db(tmp_path, monkeypatch, lat_sign=1.0, lon_sign=-1.0, name="g")
    db_reader.save_fresh_signals({"1010": 0, "1319": 0.0, "100003": 52.6})
    assert _stored(path) == (0.0, 0.0)


def test_a_southern_hemisphere_latitude_survives(tmp_path, monkeypatch):
    """Latitude uses the same resolver — a Sydney car must not be flipped into Siberia."""
    path = _db(tmp_path, monkeypatch, name="h")
    db_reader.save_fresh_signals({"2": 151.2093, "3": -33.8688, "3724": 151.2093,
                                  "3725": 33.8688, "1010": 0, "1319": 0.0, "100003": 52.6})
    assert _stored(path) == (-33.8688, 151.2093)


def test_malformed_sign_setting_does_not_crash(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch, lon_sign="not-a-number", name="i")
    sig = {k: v for k, v in LISBON_SIGNED.items() if k not in ("2", "3")}
    db_reader.save_fresh_signals(sig)
    assert _stored(path) == (38.722252, 9.139337)       # unusable setting → treated as unknown
