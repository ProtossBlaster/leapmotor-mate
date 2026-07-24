"""REEV only: the car's own "charge complete" flag is sanity-checked before being shown.

A B10 REEV raises that flag at 23 % SoC with the charge limit at 90 %, toggling it on and off
mid-charge (beta #12) — so Mate showed "Fully charged" while the car was still filling. Same
shape as the T03 declaring seat heaters it doesn't have: the car misreports, so the claim is
checked against the battery before being repeated.

Two guards keep this from ever hiding a real "complete": it only applies to REEVs, and only when
the charge limit is actually known. Pure EVs report this correctly today and must not be touched.
"""
import db as D
import db_reader


def _status(tmp_path, monkeypatch, soc, completed, *, limit=None, reev=True, name="cc"):
    path = str(tmp_path / f"{name}.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN','B10')")
    db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charge_completed, plug_connected,"
        " latitude, longitude) VALUES (1,'2026-07-24T14:08:00+00:00',?,?,1,45.0,9.0)",
        (soc, completed))
    db._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('is_reev',?)",
                     ("1" if reev else "0",))
    if limit is not None:
        db._conn.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('charge_limit_percent',?)",
            (str(limit),))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader.get_latest_status()


def test_the_real_case_is_rejected(tmp_path, monkeypatch):
    """michapr's exact numbers: the car claimed complete at 23.3 % with the limit at 90 %."""
    s = _status(tmp_path, monkeypatch, soc=23.3, completed=1, limit=90)
    assert not s["charge_completed"]
    assert s.get("charge_completed_implausible") is True


def test_a_genuine_full_charge_is_believed(tmp_path, monkeypatch):
    s = _status(tmp_path, monkeypatch, soc=90.0, completed=1, limit=90)
    assert s["charge_completed"]
    assert "charge_completed_implausible" not in s


def test_stopping_a_little_short_still_counts(tmp_path, monkeypatch):
    """Charges legitimately stop a few percent under the limit — that must stay 'complete'."""
    s = _status(tmp_path, monkeypatch, soc=78.0, completed=1, limit=90)
    assert s["charge_completed"]


def test_a_pure_ev_is_never_touched(tmp_path, monkeypatch):
    """Same implausible numbers on a BEV: left exactly as the car reported it."""
    s = _status(tmp_path, monkeypatch, soc=23.3, completed=1, limit=90, reev=False)
    assert s["charge_completed"]
    assert "charge_completed_implausible" not in s


def test_unknown_limit_leaves_the_flag_alone(tmp_path, monkeypatch):
    """No limit read from the car yet → no reference to judge against → don't interfere."""
    s = _status(tmp_path, monkeypatch, soc=23.3, completed=1, limit=None)
    assert s["charge_completed"]


def test_a_car_not_claiming_complete_is_untouched(tmp_path, monkeypatch):
    s = _status(tmp_path, monkeypatch, soc=23.3, completed=0, limit=90)
    assert not s["charge_completed"]
    assert "charge_completed_implausible" not in s
