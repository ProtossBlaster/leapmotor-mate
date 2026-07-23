"""REEV slow AC charge detection (beta #12 michapr B10, #13 ebagnoli C10).

A range-extender charging in AC at home reports a signature the BEV-tuned detector couldn't
read, established from the decrypted beta bundles back to the earliest 1.36.1 one:
  * pack current (1178) sits at ~0 while charging — the on-board charger feeds the pack by a
    path this sensor doesn't measure, so the "current ≥ 2 A" rule never fires;
  * the cable state (1149) flickers 2→1→3→2 mid-charge, and 3 was treated as unplugged, so the
    session closed and reopened on every flicker — shredding one charge into many fragments,
    each then dropped as a phantom (ΔSoC ≤ 0.05 kWh).

Net effect on a genuinely slow charge: every fragment is empty, all are dropped, the charge
vanishes. Fixed by (a) trusting the cable's own "charging" state (1149==2) with remaining-time
when current is ~0, and (b) counting 1149==3 as still-connected so the flicker keeps one
session whole. Neither touches driving (motion-gated) or a scheduled wait (1149==1).
"""
import os
import sys

sys.path.insert(0, "poller")
import db as D                       # noqa: E402
from client import _parse_signal, _is_charging, _is_plugged_in   # noqa: E402
from state_machine import StateMachine   # noqa: E402
from recorder import Recorder            # noqa: E402

_BASE = 1784000000000


def _sig(**kw):
    d = {"1": str(_BASE), "sts": str(_BASE), "1318": "10000", "3235": "55",
         "100003": "40", "1010": "0", "1319": "0", "1149": "0"}
    d.update({k: str(v) for k, v in kw.items()})
    return d


# ── the two detector-level facts, stated as assertions ───────────────────────

def test_reev_ac_charge_detected_despite_zero_current():
    # cable says "charging", a charge is in progress (remaining minutes), current ~0
    assert _is_charging(_sig(**{"1149": "2", "1178": "0.1", "1200": "340"})) is True


def test_scheduled_wait_is_not_charging():
    # cable "connected" (1), not "charging" (2) — a programmed charge waiting for its slot
    assert _is_charging(_sig(**{"1149": "1", "1178": "0.0", "1200": "280"})) is False


def test_charging_state_without_remaining_time_is_not_charging():
    # a bare 0→2→0 cable blip has no remaining-time → can't open a phantom
    assert _is_charging(_sig(**{"1149": "2", "1178": "0.0"})) is False


def test_driving_regen_is_never_charging():
    # gear D, moving, strong regen current, drive-time cable code 5
    assert _is_charging(_sig(**{"1010": "3", "1319": "50", "1149": "5", "1178": "-30"})) is False


def test_cable_state_three_counts_as_plugged_in():
    assert _is_plugged_in(_sig(**{"1149": "3", "1178": "0.5"})) is True


def test_cable_state_three_while_driving_is_not_plugged():
    assert _is_plugged_in(_sig(**{"1010": "3", "1319": "50", "1149": "3"})) is False


# ── end-to-end: a whole slow charge is ONE session, not many phantoms ────────

def _run_slow_charge(tmp_path, soc_step):
    dbp = str(tmp_path / "reev.db")
    for e in ("", "-wal", "-shm"):
        try:
            os.remove(dbp + e)
        except OSError:
            pass
    db = D.Database(dbp)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'REEVTEST','C10')")
    db._conn.commit()
    rec = Recorder(db, vehicle_id=1)
    rec._read_wallbox_energy = lambda: None      # hermetic — no live HA wallbox
    sm = StateMachine()

    def feed(off, **kw):
        s = _sig(**{"1": str(_BASE + off), "sts": str(_BASE + off), **kw})
        data = _parse_signal("REEVTEST", s)
        for ev in sm.update(data):
            rec._handle_event(ev, data)
        rec.process(data)

    # slow AC charge: cable flickers 2/1/3, current ~0, remaining ticks down, SoC creeps
    rem, soc, cyc = 340, 38.0, ["2", "1", "2", "3", "2", "1", "2", "2"]
    for i in range(90):
        rem = max(0, rem - 3)
        soc += soc_step
        feed(i * 30000, **{"1149": cyc[i % len(cyc)], "1178": ["0.1", "-2.8", "0.0", "0.5", "0.0"][i % 5],
                           "1200": str(rem), "100003": f"{soc:.2f}", "1177": "402"})
    feed(95 * 30000, **{"1149": "0", "1178": "0.0", "100003": f"{soc:.2f}"})     # unplug → close
    return db._conn.execute("SELECT COUNT(*) c, MAX(end_soc)-MIN(start_soc) span FROM charges").fetchone()


def test_a_slow_reev_charge_is_a_single_session(tmp_path):
    row = _run_slow_charge(tmp_path, soc_step=0.35)
    assert row["c"] == 1                       # one session, not a pile of fragments
    assert row["span"] > 25                    # spanning the whole 38→~70 % charge


def test_a_very_slow_reev_charge_does_not_vanish(tmp_path):
    # +0.03 %/poll: on the old code every fragment is < 0.05 kWh and all are dropped → 0 charges.
    row = _run_slow_charge(tmp_path, soc_step=0.03)
    assert row["c"] == 1
