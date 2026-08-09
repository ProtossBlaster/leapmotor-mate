"""A hole in the cloud's reporting must not make the battery look older (#241, @riri19).

`get_battery_health` estimates capacity as **energy ÷ SoC risen**. The energy is integrated over
the logged samples and — rightly — **skips any interval longer than 15 minutes**: nobody knows what
the charger did during a gap, and integrating across one would invent kilowatt-hours.

But the SoC side counted the **whole** rise, gap included. So the two halves of the fraction
measured different windows, and every minute the car went quiet pushed the estimate down. Measured
on one identical charge, 32 kWh into a 67.1 kWh pack, with a single hole in the middle:

    no hole   67.4 kWh  100.4 %
    30 min    61.1 kWh   91.0 %
    60 min    54.7 kWh   81.6 %
    120 min   42.1 kWh   62.8 %

Same energy, same SoC, same battery — read as aged, with nothing on the page to say otherwise. A
point that vanishes is noticed; a point that is quietly wrong is believed.

The fix is not a threshold or a tolerance: it is making both halves span the same window. The SoC
rise is now accumulated **only across the intervals whose energy was counted**, so a gap shrinks
the numerator and the denominator together and the ratio stays honest.
→ [[signal-absent-is-not-signal-zero]] · [[feedback-two-numbers-one-word]]
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

T0 = datetime(2026, 8, 8, 20, 0, tzinfo=timezone.utc)
MINUTES = 320                      # 5h20
SOC_FROM, SOC_TO = 42.5, 90.0
NOMINAL = 67.1


def _charge_with_hole(hole_min: int, tmp_path, monkeypatch, step: float = 1.0):
    """The same charge every time — 32 kWh, SoC 42.5 → 90.0 — with one hole in the middle."""
    path = str(tmp_path / f"h{hole_min}_{step}.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type, capacity_kwh) VALUES (1,'LFZTEST','B10',?)",
              (NOMINAL,))
    c.execute("INSERT OR REPLACE INTO settings (key,value)"
              " VALUES ('battery_capacity_nominal_kwh',?)", (str(NOMINAL),))
    h0 = (MINUTES - hole_min) // 2
    h1 = h0 + hole_min
    m = 0.0
    while m <= MINUTES:
        if not (h0 < m < h1):                       # inside the hole the car said nothing
            soc = SOC_FROM + (SOC_TO - SOC_FROM) * m / MINUTES
            c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging,"
                      " charge_voltage_v, charge_current_a, battery_min_temp, odometer_km, ready)"
                      " VALUES (1,?,?,1,240.0,25.0,22.0,10000,0)",
                      ((T0 + timedelta(minutes=m)).isoformat(), round(soc, 1)))
        m += step
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
              " energy_added_kwh, charge_type, location_type)"
              " VALUES (1,1,?,?,?,?,32.0,'AC','HOME')",
              (T0.isoformat(), (T0 + timedelta(minutes=MINUTES)).isoformat(), SOC_FROM, SOC_TO))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader.get_battery_health()


def _capacity(health):
    pts = health["points"]
    return pts[0]["capacity_kwh"] if pts else None


# ── the defect itself ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("hole", [16, 30, 60, 120])
def test_a_reporting_hole_does_not_age_the_battery(hole, tmp_path, monkeypatch):
    """🔴 RED before the fix: 64.0 / 61.1 / 54.7 / 42.1 kWh for the four holes — the same pack
    reading anywhere from 95 % to 63 % of itself depending only on how talkative the cloud was."""
    whole = _capacity(_charge_with_hole(0, tmp_path, monkeypatch))
    holed = _capacity(_charge_with_hole(hole, tmp_path, monkeypatch))
    assert whole is not None and holed is not None
    assert abs(holed - whole) <= 1.0, (
        f"a {hole}-minute hole moved the estimate {whole} → {holed} kWh")


def test_the_estimate_is_the_right_number_not_merely_a_stable_one(tmp_path, monkeypatch):
    """Agreeing with itself is not enough — both could be wrong together. 32 kWh for 47.5 points
    of a 67.1 kWh pack is 67.4 kWh of capacity, and that is what has to come out."""
    for hole in (0, 60):
        cap = _capacity(_charge_with_hole(hole, tmp_path, monkeypatch))
        assert cap is not None and 66.0 <= cap <= 69.0, f"hole={hole}: {cap} kWh"


# ── the guard that must NOT be weakened ───────────────────────────────────────

def test_the_gap_is_still_not_integrated(tmp_path, monkeypatch):
    """The fix must shrink the DENOMINATOR, never start inventing energy inside the hole. Two
    hours of silence at 6 kW would be 12 phantom kWh; the integral has to stay off them."""
    path = str(tmp_path / "guard.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST','B10')")
    for m in (0, 200):                              # two samples, 200 minutes apart
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging,"
                  " charge_voltage_v, charge_current_a) VALUES (1,?,?,1,240.0,25.0)",
                  ((T0 + timedelta(minutes=m)).isoformat(), 40.0 + m / 10))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    below = db_reader._charge_energy_below_soc(
        db_reader._get(), T0.isoformat(), (T0 + timedelta(minutes=200)).isoformat(), 95.0)
    assert below is not None
    energy, _reached, covered = below
    assert energy == 0.0, f"the hole was integrated: {energy} kWh"
    assert covered == 0.0, f"and its SoC was counted anyway: {covered} points"


def test_a_dense_charge_is_unchanged(tmp_path, monkeypatch):
    """The everyday case must not move. A reading every 30 seconds leaves only the sliver before
    the first sample uncounted, so the figure has to land where it always did."""
    cap = _capacity(_charge_with_hole(0, tmp_path, monkeypatch, step=0.5))
    assert cap is not None and 66.0 <= cap <= 69.0, cap


def test_a_soc_dip_mid_charge_does_not_shrink_the_denominator(tmp_path, monkeypatch):
    """A pack does not un-charge. The BMS wobbles by a tenth on an LFP all the time, and if those
    dips were subtracted the denominator would shrink and the capacity read HIGH — the mirror of
    the defect this file is about, and it escaped the first round of mutations because nothing
    here had a dip in it. → [[feedback-a-green-test-can-assert-the-bug]]"""
    path = str(tmp_path / "dip.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST','B10')")
    soc = 40.0
    for m in range(0, 101):
        soc += 0.2 if m % 10 else -0.2               # one tenth back every ten minutes
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging,"
                  " charge_voltage_v, charge_current_a) VALUES (1,?,?,1,240.0,25.0)",
                  ((T0 + timedelta(minutes=m)).isoformat(), round(soc, 1)))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    below = db_reader._charge_energy_below_soc(
        db_reader._get(), T0.isoformat(), (T0 + timedelta(minutes=100)).isoformat(), 95.0)
    assert below is not None
    _e, reached, covered = below
    rises = 0.2 * 90                                 # 90 up-steps, 10 down-steps
    assert covered == pytest.approx(rises, abs=0.3), (
        f"the dips were counted: {covered} instead of {rises} "
        f"(the net rise, which is NOT what we want, is {(reached or 40.0) - 40.0:.1f})")


# ── what the fix hands back, for whoever wants to show it ─────────────────────

def test_the_covered_soc_is_reported(tmp_path, monkeypatch):
    """The SoC actually measured is returned, so a caller can tell a well-observed charge from a
    barely-observed one instead of both arriving as a bare number."""
    path = str(tmp_path / "cov.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST','B10')")
    for m in range(0, 101):
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging,"
                  " charge_voltage_v, charge_current_a) VALUES (1,?,?,1,240.0,25.0)",
                  ((T0 + timedelta(minutes=m)).isoformat(), 40.0 + m * 0.2))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    below = db_reader._charge_energy_below_soc(
        db_reader._get(), T0.isoformat(), (T0 + timedelta(minutes=100)).isoformat(), 95.0)
    assert below is not None
    _e, reached, covered = below
    assert reached == pytest.approx(60.0, abs=0.1)
    assert covered == pytest.approx(20.0, abs=0.2), covered   # 40.0 → 60.0, all of it measured
