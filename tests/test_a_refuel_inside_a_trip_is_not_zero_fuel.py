"""Filling the tank DURING a drive must not erase that drive's petrol (beta #30, @pdifeo).

His bundle, swept trip by trip: on **3 of 98** the fuel level goes UP between start and end — he
stopped for petrol in the middle. On all three Mate recorded `engine_ran = False` and no litres at
all, so the drive reads as fully electric:

    30/07 04:08   32 km   fuel 73.4% → 99.7%   generator=No   litres=—   SoC 69.8 → 73.3
    03/08 03:30   36 km   fuel 80.6% → 100.0%  generator=No   litres=—   SoC 65.0 → 46.7
    25/07 05:06    6 km   fuel 36.2% → 83.7%   generator=No   litres=—   SoC 76.0 → 73.9

The first one is provably wrong. His own note for that day: *"fatto parzialmente con carburante per
un tratto iniziale (che è in salita)"* — and the battery **gains 3.5 points over 32 km of climbing**,
which is the generator's signature and nothing else's.

The mechanism is arithmetic: litres = start − end, which goes NEGATIVE on a refuel and falls into
the "nothing burned" branch. There is no independent "engine on" signal to fall back on — the
function's own docstring says the range-extender ran iff the level dropped.

So the honest answer is not zero, it is **unknown**: something was burned, we cannot say how much,
and the trip must not be counted as a zero in anyone's fuel total. → [[signal-absent-is-not-signal-zero]]
"""
import pytest

import db_reader


def _fuel(start_pct, end_pct, km, *, start_l=None, end_l=None):
    return db_reader._reev_trip_fuel(start_pct, end_pct, km, None, start_l, end_l)


def test_a_tank_that_ends_fuller_is_a_refuel_not_an_empty_drive():
    """His 30 July: 73.4% → 99.7% over 32 km."""
    out = _fuel(73.4, 99.7, 32.0, start_l=34.875, end_l=47.4)
    assert out["fuel_refuelled"] is True


def test_the_litres_stay_unknown_rather_than_zero():
    out = _fuel(73.4, 99.7, 32.0, start_l=34.875, end_l=47.4)
    assert out["fuel_used_l"] is None
    assert out["fuel_l_100km"] is None


def test_an_ordinary_generator_trip_is_untouched():
    """The common case must not learn a new state: 3.58 L burned over 34 km, his 14 August."""
    out = _fuel(54.2, 46.6, 34.0, start_l=27.1, end_l=23.52)
    assert out["fuel_refuelled"] is False
    assert out["engine_ran"] is True
    assert out["fuel_used_l"] == pytest.approx(3.58, abs=0.01)


def test_a_pure_electric_trip_is_untouched():
    """Level flat: nothing burned, nothing refuelled, and no new flag raised."""
    out = _fuel(56.3, 56.3, 12.0, start_l=28.15, end_l=28.15)
    assert out["fuel_refuelled"] is False
    assert out["engine_ran"] is False
    assert out["fuel_used_l"] is None


def test_gauge_noise_upwards_is_not_a_refuel():
    """The gauge wobbles by its own step. A tenth of a percent up is not a petrol station, and
    calling it one would put the flag on ordinary drives."""
    out = _fuel(56.3, 56.4, 12.0)
    assert out["fuel_refuelled"] is False


def test_the_percentage_path_sees_it_too():
    """Older trips have no litre counter — only the tank percentage. A refuel is just as invisible
    there, and just as wrong to read as zero."""
    out = _fuel(36.2, 83.7, 6.0)
    assert out["fuel_refuelled"] is True
    assert out["fuel_used_l"] is None


def test_a_trip_with_no_fuel_data_at_all_says_nothing():
    """A BEV, or a trip recorded before the fuel signals existed."""
    out = _fuel(None, None, 20.0)
    assert out["fuel_refuelled"] is False


# ── the trip row ──────────────────────────────────────────────────────────────
def test_the_row_says_it_instead_of_leaving_the_slot_empty():
    """A drive that burned petrol and shows nothing about petrol reads as pure electric. The dash
    keeps the slot; the tooltip carries the reason."""
    jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the row")
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "web" / "templates" / "partials" / "trip_row.html").read_text()
    start = src.index("{% if trip.fuel_refuelled %}")
    block = src[start:src.index("{% endif %}", start) + len("{% endif %}")]
    tr = json.loads((root / "web" / "locales" / "en.json").read_text())["translations"]

    env = jinja2.Environment()
    out = env.from_string(block).render(trip={"fuel_refuelled": True}, t=lambda k: tr[k])
    assert "⛽ —" in out
    assert "Refuelled during this drive" in out

    assert env.from_string(block).render(trip={"fuel_refuelled": False}, t=lambda k: tr[k]).strip() == ""
