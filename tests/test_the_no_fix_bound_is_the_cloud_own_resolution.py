"""The "(0, 0) means no GPS fix" bound is one unit of the resolution the cloud reports.

A mutation survived the suite on 24/09: `_NO_FIX_DEG` moved from 1e-6 to 1e-3 and nothing went
red. The bound decides whether a stored coordinate pair is a real position or the (0, 0) a poll
without a fix leaves behind, and it is used in two places that must agree — the Python guard and
the SQL of the fallback query. Nothing pinned how small it has to be.

1e-6 is one unit of the sixth decimal the cloud serves coordinates in, about 11 cm: anything
smaller is zero at the data's own resolution. 1e-3 is about 111 m, so a car genuinely parked
within that of the Gulf of Guinea would be read as having no fix — remote, but the point is that
the number is a statement about the data and was free to drift.
"""
import db_reader


def test_the_bound_is_one_unit_of_the_sixth_decimal():
    assert db_reader._NO_FIX_DEG == 1e-6


def test_a_position_a_metre_from_the_origin_is_still_a_fix():
    """~1.1 m at the equator. Only a pair that is zero at the cloud's own resolution is no fix."""
    assert db_reader.has_gps_fix(1e-5, 1e-5)


def test_the_exact_pair_of_zeros_is_not_a_fix():
    assert not db_reader.has_gps_fix(0.0, 0.0)


def test_a_bound_coarse_enough_to_swallow_a_real_parking_spot_is_refused():
    """The mutation that survived: at 1e-3 a car parked 100 m from (0,0) reads as no fix."""
    assert db_reader._NO_FIX_DEG < 1e-5, \
        "the bound is coarser than a metre — real positions near (0, 0) would read as no fix"
