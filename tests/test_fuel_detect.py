"""Refuel auto-detection from the car's own fuel gauge (beta #14 @gm27271).

The premise the whole feature rests on: a tank can only rise one way. Nothing recuperates into it,
nothing refills it while driving — so a rise IS a refuel and the only thing to reject is the gauge's
noise. These tests pin that rule, and just as importantly the three things that keep a detection from
turning into a nuisance: a spike is not a fill, a dismissal is permanent, and a refuel the user has
already typed by hand must not come back as a second one.

Throwaway tmp DB throughout (monkeypatched DB_PATH), never the ambient one.
"""
import sqlite3

import db_reader


def _setup_db(path, readings):
    """`readings` = [(recorded_at, fuel_level_pct), …] or [(recorded_at, pct, litres), …].

    Two-item rows leave `fuel_liters` NULL on purpose: that is a car from before v2.14.1, where the
    litres can only be the percentage against the model's assumed tank. Three-item rows carry the
    car's own counter (signal 3263) and the litres become measured.
    """
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE vehicles (id INTEGER PRIMARY KEY, vin TEXT, car_type TEXT)")
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'VINX', 'C10 REEV')")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "vehicle_id INTEGER, recorded_at TEXT, fuel_level_pct REAL, fuel_liters REAL)")
    con.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, fuel_level_pct, fuel_liters) VALUES (?,?,?,?)",
        [(1, r[0], r[1], r[2] if len(r) > 2 else None) for r in readings])
    con.commit()
    con.close()


def _db(tmp_path, monkeypatch, readings):
    dbp = str(tmp_path / "t.db")
    _setup_db(dbp, readings)
    monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    return dbp


def _t(minute):
    return f"2026-07-20T{minute // 60:02d}:{minute % 60:02d}:00+00:00"


# A drive down to 18 %, a stop at the pump, and 25 % more in the tank — then it stays there.
_A_REFUEL = [(_t(600), 24.0), (_t(660), 18.0), (_t(720), 43.0), (_t(780), 42.6), (_t(840), 42.0)]


def test_a_rise_is_a_refuel(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, _A_REFUEL)
    assert db_reader.scan_fuel_refuels(1) == 1
    (d,) = db_reader.list_fuel_detected(1)
    assert (d["fuel_before_pct"], d["fuel_after_pct"]) == (18.0, 43.0)
    assert abs(d["liters"] - 11.88) < 1e-6           # 25 % of a C10's 47.5 L tank, not 50
    # The instant is the first reading that SHOWED the new level, and the window opens at the last
    # one that still showed the old — because that is genuinely all we know.
    assert (d["ts_from"], d["ts"]) == (_t(660), _t(720))


def test_the_normal_life_of_a_tank_is_not_a_refuel(tmp_path, monkeypatch):
    """Driving only ever empties it. A trail that just goes down must produce nothing at all."""
    _db(tmp_path, monkeypatch, [(_t(m), 90.0 - m / 60) for m in range(0, 1200, 60)])
    assert db_reader.scan_fuel_refuels(1) == 0
    assert db_reader.list_fuel_detected(1) == []


def test_gauge_noise_stays_under_the_floor(tmp_path, monkeypatch):
    """A float gauge wobbles — parking nose-up and nose-down is not a trip to the pump. Below the
    floor (2 % ≈ 1 L) nothing is claimed."""
    _db(tmp_path, monkeypatch, [(_t(0), 40.0), (_t(60), 41.2), (_t(120), 40.4), (_t(180), 41.5)])
    assert db_reader.scan_fuel_refuels(1) == 0


def test_a_spike_that_falls_back_is_not_a_fill(tmp_path, monkeypatch):
    """One high sample between two ordinary ones is the sensor, not a pump: the level has to HOLD."""
    _db(tmp_path, monkeypatch, [(_t(0), 30.0), (_t(60), 29.5), (_t(120), 55.0), (_t(180), 29.0),
                                (_t(240), 28.5)])
    assert db_reader.scan_fuel_refuels(1) == 0


def test_two_refuels_are_two_detections(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        ("2026-07-02T08:00:00+00:00", 20.0), ("2026-07-02T09:00:00+00:00", 70.0),
        ("2026-07-02T10:00:00+00:00", 69.0),
        ("2026-07-20T08:00:00+00:00", 15.0), ("2026-07-20T09:00:00+00:00", 60.0),
        ("2026-07-20T10:00:00+00:00", 59.0),
    ])
    assert db_reader.scan_fuel_refuels(1) == 2
    assert [d["fuel_after_pct"] for d in db_reader.list_fuel_detected(1)] == [60.0, 70.0]  # newest first


def test_scanning_twice_does_not_duplicate(tmp_path, monkeypatch):
    """The page runs the scan every time it is opened; the same rise must stay one detection."""
    _db(tmp_path, monkeypatch, _A_REFUEL)
    assert db_reader.scan_fuel_refuels(1) == 1
    assert db_reader.scan_fuel_refuels(1) == 0
    assert len(db_reader.list_fuel_detected(1)) == 1


def test_a_dismissal_is_permanent(tmp_path, monkeypatch):
    """"That wasn't a refuel" has to stick — the scan reads the same positions again for ever."""
    _db(tmp_path, monkeypatch, _A_REFUEL)
    db_reader.scan_fuel_refuels(1)
    (d,) = db_reader.list_fuel_detected(1)
    assert db_reader.dismiss_fuel_detected(d["id"]) is True
    assert db_reader.list_fuel_detected(1) == []
    db_reader.set_setting("fuel_scan_watermark", "")      # force a full re-walk
    assert db_reader.scan_fuel_refuels(1) == 0
    assert db_reader.list_fuel_detected(1) == []


def test_a_refuel_typed_by_hand_is_not_offered_again(tmp_path, monkeypatch):
    """He filled up and logged it himself before ever opening the page. The rise is still in the
    gauge — offering it as a second, unconfirmed refuel is exactly the duplicate to avoid."""
    _db(tmp_path, monkeypatch, _A_REFUEL)
    db_reader.add_fuel_purchase(_t(700), liters=12.4, price_per_l=1.789)
    assert db_reader.scan_fuel_refuels(1) == 0
    assert db_reader.list_fuel_detected(1) == []


def test_confirming_files_it_at_the_detected_instant(tmp_path, monkeypatch):
    """The point of confirming rather than retyping: the refuel lands at the moment it HAPPENED, so
    its residual is the exact reading before it — not whatever the tank held when he got round to
    typing. Here 18 %, which a hand-typed "now" would have recorded as 42 %."""
    _db(tmp_path, monkeypatch, _A_REFUEL)
    db_reader.scan_fuel_refuels(1)
    (d,) = db_reader.list_fuel_detected(1)
    pid = db_reader.confirm_fuel_detected(d["id"], liters=12.4, price_per_l=1.789)
    assert pid is not None
    (p,) = db_reader.list_fuel_purchases()
    assert p["ts"] == _t(720)
    assert abs(p["liters"] - 12.4) < 1e-6                 # the pump's number beats the estimate
    assert abs(p["fuel_before_pct"] - 18.0) < 1e-6
    assert abs(p["total_cost"] - round(12.4 * 1.789, 2)) < 1e-6
    assert db_reader.list_fuel_detected(1) == []          # gone from the pending list


def test_confirming_keeps_the_estimate_when_he_does_not_correct_it(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, _A_REFUEL)
    db_reader.scan_fuel_refuels(1)
    (d,) = db_reader.list_fuel_detected(1)
    db_reader.confirm_fuel_detected(d["id"], liters=None, total_cost=22.0)
    (p,) = db_reader.list_fuel_purchases()
    assert abs(p["liters"] - 11.88) < 1e-6
    assert abs(p["price_per_l"] - 22.0 / 11.88) < 1e-4


def test_a_confirmed_refuel_is_not_re_detected(tmp_path, monkeypatch):
    """Confirming deletes the detection but leaves the rise in the gauge. A full re-walk must see
    the purchase it became and stay quiet."""
    _db(tmp_path, monkeypatch, _A_REFUEL)
    db_reader.scan_fuel_refuels(1)
    (d,) = db_reader.list_fuel_detected(1)
    db_reader.confirm_fuel_detected(d["id"], price_per_l=1.789)
    db_reader.set_setting("fuel_scan_watermark", "")
    assert db_reader.scan_fuel_refuels(1) == 0
    assert db_reader.list_fuel_detected(1) == []


def test_a_bev_has_nothing_to_scan(tmp_path, monkeypatch):
    """No fuel column values at all — the query returns nothing and the scan must not throw."""
    dbp = str(tmp_path / "t.db")
    _setup_db(dbp, [])
    con = sqlite3.connect(dbp)
    con.executemany("INSERT INTO positions (vehicle_id, recorded_at, fuel_level_pct) VALUES (?,?,?)",
                    [(1, _t(m), None) for m in (0, 60, 120)])
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    assert db_reader.scan_fuel_refuels(1) == 0
    assert db_reader.list_fuel_detected(1) == []


def test_the_newest_pair_is_left_for_the_next_scan(tmp_path, monkeypatch):
    """A rise seen on the very last reading has nothing after it to confirm with. It must not be
    claimed yet — and it must not be lost either: the watermark stops short so the next scan,
    once one more reading has arrived, picks it up."""
    dbp = _db(tmp_path, monkeypatch, [(_t(0), 20.0), (_t(60), 19.0), (_t(120), 60.0)])
    assert db_reader.scan_fuel_refuels(1) == 0
    con = sqlite3.connect(dbp)
    con.execute("INSERT INTO positions (vehicle_id, recorded_at, fuel_level_pct) VALUES (1,?,?)",
                (_t(180), 59.5))
    con.commit()
    con.close()
    assert db_reader.scan_fuel_refuels(1) == 1
