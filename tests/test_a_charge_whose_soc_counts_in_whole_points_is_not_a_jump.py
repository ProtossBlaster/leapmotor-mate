"""A charge whose SoC counts in whole points is not a BMS jump every time it ticks (#435).

The SoH page leaves out an AC charge whose SoC rose faster than 0.8 %/min between two samples: a
BMS snap adds points without energy. The T03 reports its SoC in whole points (no precise SoC,
signal 100003), so every tick is one full point between two polls 30 s apart, about 2 %/min. In
@matttiaromano's bundle 123 of 831 charging pairs read that way, every one of his AC charges was
left out as a jump, and the page had no figure after four charges.

A rise of one step of the SoC's own resolution is a tick, never a jump. The step is read from the
charge itself: whole points when no sample carries a fraction, a tenth otherwise. A real snap of
several points still counts, on a whole-point SoC and on a precise one.
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader

START = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)


def _seed(tmp_path, monkeypatch, socs, step_s=30, amps=-17.3):
    """One AC charge, a sample every `step_s` seconds at 368 V × `amps` (6.37 kW by default), SoC as given."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(36.0)
    end = START + timedelta(seconds=step_s * (len(socs) - 1))
    db._conn.execute(
        "INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type) "
        "VALUES (1,1,?,?,?,?,'AC')", (START.isoformat(), end.isoformat(), socs[0], socs[-1]))
    for k, soc in enumerate(socs):
        db._conn.execute(
            "INSERT INTO positions (vehicle_id,recorded_at,soc,charging,charge_voltage_v,charge_current_a)"
            " VALUES (1,?,?,1,368,?)", ((START + timedelta(seconds=step_s * k)).isoformat(), soc, amps))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return db_reader.get_battery_health()


def _ticking(first, last, polls_per_point=7):
    """A whole-point SoC climbing one point every `polls_per_point` polls, as the T03's does at 6.4 kW."""
    return [float(first + k // polls_per_point) for k in range((last - first) * polls_per_point + 1)]


def test_a_whole_point_soc_that_ticks_once_per_point_is_measured(tmp_path, monkeypatch):
    h = _seed(tmp_path, monkeypatch, _ticking(20, 40))
    assert h["soc_jump_count"] == 0, h["points"]
    assert h["sample_count"] == 1
    assert h["points"][0]["excluded"] is False


def test_a_whole_point_soc_that_leaps_three_points_is_still_a_jump(tmp_path, monkeypatch):
    socs = _ticking(20, 30) + [33.0] + _ticking(34, 46)[1:]
    h = _seed(tmp_path, monkeypatch, socs)
    assert h["points"][0]["exclude_reason"] == "soc_jump"


def test_a_precise_soc_that_leaps_under_a_point_is_still_a_jump(tmp_path, monkeypatch):
    """On a precise SoC, 0.9 points in 30 s are nine steps at once: the snap the check is for. Under
    one whole point, so reading every SoC as whole points would let it through."""
    socs = [round(50 + 0.1 * k, 1) for k in range(121)]        # 0.2 %/min for an hour, 50.0 → 62.0
    socs = socs[:60] + [round(s + 0.8, 1) for s in socs[60:]]  # +0.9 between two samples 30 s apart
    h = _seed(tmp_path, monkeypatch, socs, amps=-10.0)         # 3.7 kW: about 29 kWh over 12.8 points
    assert h["points"][0]["exclude_reason"] == "soc_jump"
