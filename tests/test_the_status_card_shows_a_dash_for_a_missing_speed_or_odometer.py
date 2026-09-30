"""The status card shows a dash for a speed or odometer the car did not send, not a 0.

The card shows the latest `positions` row, which stores such a reading as NULL; the speed tile
printed it as 0 km/h and the odometer row as 0 km. A reading the car has but this poll missed reads
"—" on the card, as the temperatures below it already do.

Skips with the Overview tests it takes its page from, where httpx is missing.
"""
import re
import time

import client  # poller/client.py
import pytest
import recorder as R
from test_the_overview_says_whether_its_data_can_be_trusted import (  # noqa: F401  (fixture)
    VIN,
    web,
)

_ABSENT = object()


def _card(web, speed=_ABSENT, odometer=_ABSENT):
    """The card after one parked poll, parsed and saved as the poller does: (speed, odometer)."""
    poller, http = web
    sig = {"1": int(time.time() * 1000), "100003": 60.0, "1010": 0, "3": 45.0, "2": 9.0}
    if speed is not _ABSENT:
        sig["1319"] = speed
    if odometer is not _ABSENT:
        sig["1318"] = odometer
    R.Recorder(poller, vehicle_id=1).process(client._parse_signal(VIN, sig))
    html = http.get("/api/status-card").text

    def text(pattern):
        return " ".join(re.sub(r"<[^>]+>", " ", re.search(pattern, html, re.DOTALL).group(1)).split())

    return (text(r'stat-label">Speed</div>\s*<div[^>]*>(.*?)</div>'),
            text(r'>Odometer</span>\s*<span[^>]*>(.*?)</span>'))


@pytest.mark.parametrize("missing", [_ABSENT, None, ""], ids=["absent", "None", "empty"])
def test_neither_reads_zero_when_the_car_did_not_send_it(web, missing):
    assert _card(web, speed=missing, odometer=missing) == ("—", "—")


def test_a_missing_speed_leaves_the_odometer_alone(web):
    speed, odometer = _card(web, odometer=12345.6)
    assert speed == "—" and odometer != "—"


def test_a_missing_odometer_leaves_the_speed_alone(web):
    assert _card(web, speed=37.0) == ("37 km/h", "—")


def test_a_measured_zero_is_still_printed(web):
    assert _card(web, speed=0.0, odometer=0.0) == ("0 km/h", "0 km")
