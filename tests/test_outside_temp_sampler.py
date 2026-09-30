"""The live outside-temperature sampler caches hard: a fresh Open-Meteo lookup happens only when the
reading is stale or the car has moved far, so a parked car makes no calls at all (Open-Meteo's free
tier is ~10 000 req/day per IP). A failed lookup keeps the old value and waits as long as a good one
is trusted before it asks again.
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
    assert s.sample(45.0, 9.0, 60.0) is None            # the next poll does not ask again
    assert s.sample(45.0, 9.0, OT._RETRY_S) == 12.0     # the wait is over → retries, succeeds


def test_a_refused_lookup_is_not_asked_again_on_every_poll():
    # 29/09: Open-Meteo's daily cap per IP ran out and every 30 s poll asked again, 432 times in 4 h
    calls = []
    s = OT.OutsideTempSampler(fetch=lambda la, lo: (calls.append(1), None)[1])
    for i in range(int(OT._RETRY_S // 30)):             # a poll every 30 s, driving 50 km meanwhile
        s.sample(45.0, 9.0 + i * 0.02, i * 30.0)
    assert len(calls) == 1
    s.sample(45.0, 9.0, OT._RETRY_S)
    assert len(calls) == 2


def test_a_failure_keeps_the_last_good_reading_through_the_wait():
    seq = [10.0, None]
    calls = []
    s = OT.OutsideTempSampler(fetch=lambda la, lo: (calls.append(1), seq.pop(0))[1])
    assert s.sample(45.0, 9.0, 0.0) == 10.0
    assert s.sample(45.0, 9.0, OT._MAX_AGE_S) == 10.0   # stale → asks, fails → the old value stays
    assert s.sample(45.0, 9.0, OT._MAX_AGE_S + 60) == 10.0
    assert len(calls) == 2


def test_after_a_retry_succeeds_the_ordinary_cache_applies():
    seq = [None, 12.0, 14.0]
    calls = []
    s = OT.OutsideTempSampler(fetch=lambda la, lo: (calls.append(1), seq.pop(0))[1])
    s.sample(45.0, 9.0, 0.0)
    assert s.sample(45.0, 9.0, OT._RETRY_S) == 12.0
    assert s.sample(45.0, 9.2, OT._RETRY_S + 60) == 14.0   # ~16 km on, a minute later: asks at once
    assert len(calls) == 3


def test_a_clock_set_back_does_not_stretch_the_wait():
    calls = []
    s = OT.OutsideTempSampler(fetch=lambda la, lo: (calls.append(1), None)[1])
    s.sample(45.0, 9.0, 10_000.0)
    s.sample(45.0, 9.0, 10_000.0 - 2 * 3600)          # the host clock set back two hours
    assert len(calls) == 2


def test_a_success_after_the_clock_went_back_ends_the_wait():
    seq = [None, 12.0, 14.0]
    calls = []
    s = OT.OutsideTempSampler(fetch=lambda la, lo: (calls.append(1), seq.pop(0))[1])
    s.sample(45.0, 9.0, 10_000.0)
    assert s.sample(45.0, 9.0, 10_000.0 - 120) == 12.0      # the clock set back two minutes → asks
    assert s.sample(45.0, 9.2, 10_000.0 + 600) == 14.0      # ten minutes and ~16 km on: asks at once
    assert len(calls) == 3
