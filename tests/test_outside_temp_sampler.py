"""The live outside-temperature sampler caches hard: a fresh Open-Meteo lookup happens only when the
reading is stale or the car has moved far, so a parked car makes no calls at all (Open-Meteo's free
tier is ~10 000 req/day per IP). A failed lookup keeps the old value and retries next poll.
"""
import outside_temp as OT


def test_no_cache_refetches():
    assert OT._should_refetch(None, None, None, 45.0, 9.0, 1000.0) is True


def test_fresh_and_still_does_not_refetch():
    assert OT._should_refetch(1000.0, 45.0, 9.0, 45.0, 9.0, 1000.0 + 60) is False


def test_stale_refetches():
    assert OT._should_refetch(1000.0, 45.0, 9.0, 45.0, 9.0, 1000.0 + OT._MAX_AGE_S) is True


def test_moved_far_refetches():
    # ~0.2° of longitude at 45°N is ~16 km — over the 10 km threshold
    assert OT._should_refetch(1000.0, 45.0, 9.0, 45.0, 9.2, 1000.0 + 60) is True


def test_sampler_hits_the_network_once_then_serves_the_cache():
    calls = []

    def fetch(lat, lon):
        calls.append((lat, lon))
        return 21.5

    s = OT.OutsideTempSampler(fetch=fetch)
    assert s.sample(45.0, 9.0, 1000.0) == 21.5          # first: fetch
    assert s.sample(45.0, 9.0, 1000.0 + 300) == 21.5    # 5 min later, not moved: cache
    assert len(calls) == 1


def test_a_parked_car_never_calls_again():
    calls = []
    s = OT.OutsideTempSampler(fetch=lambda la, lo: (calls.append(1), 10.0)[1])
    s.sample(45.0, 9.0, 0.0)
    for m in range(1, 20):                              # a poll a minute for 19 more minutes, parked
        s.sample(45.0, 9.0, m * 60.0)
    assert len(calls) == 1


def test_no_gps_keeps_the_last_reading_without_calling():
    calls = []
    s = OT.OutsideTempSampler(fetch=lambda la, lo: (calls.append(1), 7.0)[1])
    s.sample(45.0, 9.0, 0.0)
    assert s.sample(None, None, 10_000.0) == 7.0        # no fix → keep last, don't call
    assert len(calls) == 1


def test_a_failed_lookup_keeps_the_old_value_and_retries():
    seq = [None, 12.0]
    s = OT.OutsideTempSampler(fetch=lambda la, lo: seq.pop(0))
    assert s.sample(45.0, 9.0, 0.0) is None             # first fetch fails
    assert s.sample(45.0, 9.0, 60.0) == 12.0            # anchor never set → retries, succeeds
