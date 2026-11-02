"""A trip's regen counts each reading for the time it stands for, not for the poll interval.

The pack current is read once per poll and multiplied by a duration. Polls start one interval after
the previous one ENDED, so on a real install they arrive every interval plus the time a poll takes,
and some cars send a fresh frame only every few polls (the repeats in between are not counted).
Weighting every fresh reading by the bare interval left the rest of the time out of the regen.

The time a reading stands for is the time since the previous fresh frame, on the car's clock. Where
that cannot be told (a frame without a clock or older than the one before it, the next fresh frame after
either, a silence longer than crash recovery bridges) the reading counts one interval, as it always did.
"""
from dataclasses import replace
from datetime import timedelta

import pytest

from test_a_trip_ends_when_the_car_last_spoke import T0, _ms, _vd, rig  # noqa: F401  (rig: fixture)

REGEN_KW = 8.0


def _braking(ts, odo):
    return replace(_vd(ts=ts, odo=odo), charge_current_a=-20.0, charge_voltage_v=400.0, charge_power_kw=REGEN_KW)


def _drive(rig, frame_offsets_s, cadence_s=10):
    """A parked frame, a drive whose frames sit at these car-clock offsets, then the car parked."""
    db, _, poll, _ = rig
    poll(0, _vd(ts=_ms(T0), gear="P", speed=0.0))
    odo, seen = 1000, {}
    for off in frame_offsets_s:
        if off is None or off not in seen:
            odo += 1
            seen.setdefault(off, odo)
        # A repeated frame is the same frame: its odometer too.
        poll(cadence_s, _braking(None if off is None else _ms(T0 + timedelta(seconds=off)),
                                 odo if off is None else seen[off]))
    end = T0 + timedelta(seconds=10_000)
    for i in range(8):
        poll(60, _vd(ts=_ms(end + timedelta(minutes=i)), odo=odo, gear="P", speed=0.0))
    return db._conn.execute("SELECT regen_kwh FROM trips ORDER BY id DESC LIMIT 1").fetchone()[0]


@pytest.mark.parametrize("frame_every_s, offsets", [
    pytest.param(11, [11 * i for i in range(1, 31)], id="polls that come late"),
    pytest.param(45, [45 * (i // 4 + 1) for i in range(120)], id="a car that sends fewer frames"),
])
def test_a_reading_counts_the_time_since_the_previous_frame(rig, frame_every_s, offsets):
    """Polls every 11 s at a 10 s interval: 30 fresh frames, each standing for the time since the last."""
    assert _drive(rig, offsets, cadence_s=11) == pytest.approx(
        REGEN_KW * 30 * frame_every_s / 3600, abs=0.001)


@pytest.mark.parametrize("offsets, seconds", [
    pytest.param([11, None, 33, 44, 55, 66], 11 + 10 + 10 + 11 + 11 + 11, id="a frame without a clock"),
    pytest.param([11, None, 11, 44, 55, 66], 11 + 10 + 10 + 11 + 11,
                 id="a repeat of the frame before one without a clock"),
    pytest.param([11, 22, 33, 27, 44, 55], 11 + 11 + 11 + 10 + 10 + 11, id="an older frame served late"),
    pytest.param([11, 22, 33, 27, 16, 44, 55], 11 + 11 + 11 + 10 + 10 + 10 + 11, id="two steps back"),
    pytest.param([11, 22, 33, 27, 27, 16, 44, 55], 11 + 11 + 11 + 10 + 10 + 10 + 11,
                 id="two steps back, a repeat between"),
])
def test_no_time_is_counted_twice(rig, offsets, seconds):
    """A frame that cannot be placed after the previous one counts one interval, and the next fresh
    frame cannot measure across it, so it counts one interval too; a repeat of an older frame, not
    counted, does not give it a start either. After that, frames 11 s apart are measured again."""
    assert _drive(rig, offsets, cadence_s=11) == pytest.approx(REGEN_KW * seconds / 3600, abs=0.001)


@pytest.mark.parametrize("offsets", [
    pytest.param([None] * 6, id="a frame without a clock"),
    pytest.param([10, 20, 30, 5, 15, 25], id="a clock that went back"),
    pytest.param([10, 20, 30, 630, 640, 650], id="a ten-minute silence"),
])
def test_a_reading_with_no_measurable_gap_counts_one_interval(rig, offsets):
    assert _drive(rig, offsets, cadence_s=11) == pytest.approx(REGEN_KW * 6 * 10 / 3600, abs=0.001)
