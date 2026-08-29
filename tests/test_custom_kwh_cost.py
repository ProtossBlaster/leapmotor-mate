"""Custom-kWh pricing (beta #13, @ebagnoli): the price is fixed, the BILLED kWh come from HA.

He has solar. His electricity costs 0.24 and never moves — what moves is how much of a charge he
actually bought, because the rest came off his roof, and only his own HA helper knows the split.
Twice we answered "use Dynamic mode" and twice he said we had not understood; he was right. Dynamic
varies the PRICE over the power curve. What he has is a number of kWh, already worked out, and what
he asked for is one multiplication: that figure × his fixed price.

So: at the end of the charge Mate reads the entity's value and multiplies. Two things this must
never do, both checked below — price on anything other than that value, and let that value anywhere
near the ENERGY Mate reports. Those kWh are a payment fact, not what reached the battery.

Fixture mirrors test_dynamic_price_cost.py: a 2h charge, constant 5.0 kW, 10.0 kWh measured. Pure
db_reader with ha_client.get_history monkeypatched → no network, CI-safe.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import db_reader
import ha_client


T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
PRICE = 0.24          # Enrico's fixed tariff
MEASURED_KWH = 10.0   # what the charge really delivered


def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE charges (id INT, location_type TEXT, energy_added_kwh REAL, "
                "cost REAL, ac_energy_kwh REAL, started_at TEXT, ended_at TEXT, "
                "vehicle_id INTEGER DEFAULT 1)")
    con.execute("CREATE TABLE positions (recorded_at TEXT, charging INT, "
                "charge_voltage_v REAL, charge_current_a REAL)")
    for i in range(9):
        con.execute("INSERT INTO positions VALUES (?,1,250,20)",
                    ((T0 + timedelta(minutes=15 * i)).isoformat(),))
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    con.commit()
    return con


def _charge(ended=True):
    return {
        "location_type": "HOME", "energy_added_kwh": MEASURED_KWH, "ac_energy_kwh": None,
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(hours=2)).isoformat() if ended else None,
    }


def _setup(monkeypatch, con, entity_id="sensor.kwh_da_rete", base_price=PRICE):
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "get_cost_config",
                        lambda: {"mode": "flat", "modes": {"HOME": "custom_kwh"}, "bands": []})
    monkeypatch.setattr(db_reader, "get_charge_prices",
                        lambda: {"price_home_kwh": base_price} if base_price else {})
    monkeypatch.setattr(db_reader, "get_custom_kwh_entity_for", lambda ctype: entity_id)


def _ts(minutes):
    return (T0 + timedelta(minutes=minutes)).timestamp()


# ── the scenario he actually described ──────────────────────────────────────────────────────────
def test_the_charge_is_priced_on_the_sensor_not_on_the_energy(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    # His helper says 7 of the 10 kWh were bought; the other 3 came off the roof.
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(120), 7.0)])
    assert db_reader.compute_cost(_charge()) == 1.68          # 7 × 0.24
    assert db_reader.compute_cost(_charge()) != round(MEASURED_KWH * PRICE, 2)   # not 2.40


def test_a_fully_solar_charge_costs_nothing(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(120), 0.0)])
    # Zero is a PRICE, not a missing value: a charge that took nothing from the grid cost nothing,
    # and falling back to the measured energy here would invent 2.40 € of electricity he never
    # bought. → signal-absent-is-not-signal-zero, the mirror case.
    assert db_reader.compute_cost(_charge()) == 0.0


def test_the_value_read_is_the_last_one_in_the_window(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    # A helper that counts up during the session: what is owed is where it ENDED, not where it
    # started and not the average of the two.
    monkeypatch.setattr(ha_client, "get_history",
                        lambda eid, lo, hi: [(_ts(0), 0.0), (_ts(60), 3.0), (_ts(120), 7.0)])
    assert db_reader.compute_cost(_charge()) == 1.68


# ── every way it can fail, and the one answer to all of them ────────────────────────────────────
def test_no_entity_configured_prices_on_the_measured_energy(monkeypatch):
    con = _db()
    _setup(monkeypatch, con, entity_id="")
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(120), 7.0)])
    assert db_reader.compute_cost(_charge()) == 2.40          # 10 × 0.24


def test_home_assistant_silent_prices_on_the_measured_energy(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [])
    assert db_reader.compute_cost(_charge()) == 2.40


def test_a_negative_reading_is_not_trusted(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    # Export counted as negative import, a counter reset read wrong: whatever it is, it is not a
    # number of kWh to bill, and a negative cost would be worse than an approximate one.
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(120), -4.0)])
    assert db_reader.compute_cost(_charge()) == 2.40


def test_a_charge_still_running_is_priced_flat(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(60), 3.0)])
    # There is no "end of the charge" to read yet, and half a session's kWh is not the answer.
    assert db_reader.compute_cost(_charge(ended=False)) == 2.40


# ── the two invariants ──────────────────────────────────────────────────────────────────────────
def test_the_energy_mate_reports_is_untouched(monkeypatch):
    """The whole point, and the line Silvio drew on 04/08: a figure that prices a charge must not
    become the energy the charge is said to have delivered. Two quantities under one word is the
    defect this feature would otherwise re-introduce."""
    con = _db()
    _setup(monkeypatch, con)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(120), 7.0)])
    charge = _charge()
    assert db_reader.compute_cost(charge) == 1.68
    assert db_reader._billed_kwh(charge) == MEASURED_KWH


def test_custom_kwh_is_home_only():
    assert db_reader._mode_allowed("HOME", "custom_kwh")
    for away in ("AC", "FAST", "HPC"):
        assert not db_reader._mode_allowed(away, "custom_kwh"), (
            f"{away} is billed by its operator — a helper of ours has no business pricing it")
