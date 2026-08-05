"""The period strip's kWh/100 km divides by the kilometres getEC actually covers.

getEC is not a gauge you can always read: it arrives with a later poll, and it is simply absent on
everything Mate recorded before the feature existed. On the real B10 this was measured against,
123 finished trips of 323 carry no `ec_kwh` at all — 1016 km of 1824.

So summing the kWh we have and dividing by every kilometre driven is not a small imprecision: it is
a consumption reported at a fraction of the truth, in confident black and white. Measured on that
same database through the released v3.8.1 and the branch, May 2026 would have printed **0.4
kWh/100 km** — 20.0 over 5 metered kilometres, spread across 281 driven.

A missing signal is not a zero. The kWh and the distance move together or neither moves, and the
strip carries the distance the figure speaks for so the template can say when it is not all of it.
"""
import db_reader


def _trip(km, ec=None, soc=None, eff=None):
    t = {"distance_km": km, "ec_kwh": ec, "efficiency_kwh_100km": eff}
    if soc:
        t["start_soc"], t["end_soc"] = soc
    return t


def test_a_trip_without_getec_does_not_dilute_the_figure():
    """The one that matters. Both trips are driven; only one was metered."""
    tot = db_reader.trips_totals([_trip(100.0, ec=20.0), _trip(100.0, ec=None)])
    assert tot["km"] == 200.0, "the distance shown is still every kilometre driven"
    assert tot["kwh_100km"] == 20.0, "20 kWh over the 100 km they were measured on"
    assert tot["kwh_100km_km"] == 100.0, "and the strip says which 100 km"


def test_with_getec_everywhere_it_is_the_whole_distance():
    """The control: nothing is being quietly dropped when the readings are all there."""
    tot = db_reader.trips_totals([_trip(100.0, ec=20.0), _trip(100.0, ec=10.0)])
    assert tot["kwh_100km"] == 15.0
    assert tot["kwh_100km_km"] == 200.0


def test_no_getec_at_all_shows_nothing_not_a_zero():
    """A period Mate has no reading for has no electric figure — not 0.0, which reads as "you used
    none" rather than "we do not know"."""
    tot = db_reader.trips_totals([_trip(100.0), _trip(50.0)])
    assert tot["kwh_100km"] is None
    assert tot["kwh_100km_km"] is None


def test_the_real_may_2026_shape_does_not_print_a_fraction():
    """The measured case, reduced: 5 metered km inside 281 driven. Diluted it read 0.4."""
    trips = [_trip(5.0, ec=1.0)] + [_trip(23.0) for _ in range(12)]
    tot = db_reader.trips_totals(trips)
    assert tot["km"] == 281.0
    assert tot["kwh_100km"] == 20.0
    assert tot["kwh_100km_km"] == 5.0


def test_the_fuel_side_still_divides_by_every_kilometre():
    """Litres come off a gauge every trip has, so they keep the whole distance — this is the one
    place the two denominators are deliberately different, and it must not drift."""
    tot = db_reader.trips_totals([{"distance_km": 100.0, "fuel_used_l": 8.0, "ec_kwh": 20.0},
                                  {"distance_km": 100.0, "fuel_used_l": 8.0}])
    assert tot["fuel_l_100km"] == 8.0, "16 L over all 200 km"
    assert tot["kwh_100km"] == 20.0, "…and 20 kWh over the 100 that were metered"


def test_a_zero_length_trip_carrying_a_reading_adds_neither():
    """A reading with no distance to divide it by would inflate the numerator alone."""
    tot = db_reader.trips_totals([_trip(100.0, ec=20.0), _trip(0.0, ec=5.0)])
    assert tot["kwh_100km"] == 20.0
    assert tot["kwh_100km_km"] == 100.0


def test_the_half_kilometre_floor_is_on_the_metered_distance():
    """A 200 m metered hop inside a 300 km month must not become the month's consumption."""
    tot = db_reader.trips_totals([_trip(0.2, ec=0.1)] + [_trip(100.0) for _ in range(3)])
    assert tot["kwh_100km"] is None
    assert tot["km"] == 300.2
