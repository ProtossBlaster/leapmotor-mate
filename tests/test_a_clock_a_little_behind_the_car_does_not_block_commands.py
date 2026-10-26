"""A computer clock a little behind the car's does not block commands (D#437, @Letraz).

Before a command the bridge checks the car's latest reading, and the check refused any reading dated
after the computer's clock: `state_timestamp_invalid`. @Letraz's Windows PC, synchronised with an NTP
server, ran behind the time on the car's readings by up to 25 s (his bundle: the poller logged ages from
−1 to −25 s on most days). So every command sent while the car was awake was refused, and one sent while
it slept, with an old reading, went through.

A reading up to a minute ahead of the clock is now taken as current. One further ahead is still refused.
"""
import time

import pytest

from test_a_t03_is_read_as_a_t03 import T03_MAP, _commanded, _t03_to_command, bridge  # noqa: F401


def _ahead(seconds):
    return dict(T03_MAP, collectTimeMs=int((time.time() + seconds) * 1000))


@pytest.mark.parametrize("seconds", [1, 15, 25, 55])
def test_a_reading_a_little_ahead_of_the_clock_lets_the_command_go(tmp_path, monkeypatch, seconds):
    api, sent = _t03_to_command(tmp_path, monkeypatch, _ahead(seconds))
    _commanded(api)
    assert [p for p in sent if "appremotectl" in p], "the command never left"


@pytest.mark.parametrize("seconds", [90, 3600])
def test_a_reading_far_ahead_of_the_clock_is_still_refused(tmp_path, monkeypatch, seconds):
    api, sent = _t03_to_command(tmp_path, monkeypatch, _ahead(seconds))
    with pytest.raises(bridge.LeapmotorApiError, match="state_timestamp_invalid"):
        _commanded(api)
    assert not [p for p in sent if "appremotectl" in p]
