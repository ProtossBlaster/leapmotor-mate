"""The wallbox live card's "Session energy" is the SESSION, not the meter's lifetime total (beta #37).

@michapr maps a cumulative `sensor.tasmota_energy_total_4` as the energy role — the reading Mate
already treats as a counter, deltaing it start→stop for each charge. The live tile, though, printed
that reading raw: 162.58 kWh, the meter's whole life, under a label that says "Session energy". Any
`_total` sensor (Tasmota, Shelly, most smart plugs) reads this way; it only looked plausible while
the total was small. The fix reads the figure the poller already maintains on the open charge —
`ac_energy_kwh`, reset-safe — and shows "—" when no charge is open, which is the honest answer when
the car is asleep and Mate never opened a session.
"""
import pathlib

import db as D          # the POLLER db owns the charges schema; db_reader reads it
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN_PY = (ROOT / "web" / "main.py").read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    database._conn.commit()
    return database


def _charge(db, ac_energy, ended=None):
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, ac_energy_kwh) VALUES (1,?,?,?)",
        ("2026-08-26T10:00:00+00:00", ended, ac_energy))
    db._conn.commit()


def test_open_charge_session_energy_is_the_running_ac_energy(db):
    _charge(db, ac_energy=3.4)                 # open (ended_at NULL) — a session in progress
    assert db_reader.open_charge_session_energy() == 3.4


def test_no_open_charge_gives_none(db):
    """Nothing charging — the tile must read "—", not the meter's lifetime total."""
    assert db_reader.open_charge_session_energy() is None


def test_a_closed_charge_is_not_the_open_session(db):
    """A finished charge carries its energy, but it is not the session in progress."""
    _charge(db, ac_energy=8.56, ended="2026-08-26T12:00:00+00:00")
    assert db_reader.open_charge_session_energy() is None


def test_the_live_endpoint_reads_the_session_not_the_raw_meter():
    """The tile must be fed from Mate's own session accumulator, never ha_client's raw meter reading
    (which for a cumulative sensor is the whole life of the meter — beta #37, @michapr)."""
    assert 'wb["energy_kwh"] = db_reader.open_charge_session_energy()' in MAIN_PY
