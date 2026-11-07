"""The row the web stores after a command reads the cable and the charge as the poller does.

After a command, and on the refresh button, the web fetches the car's frame and stores it as a position
row between two polls. It judged that frame with its own copy of the poller's rules, which had drifted:
a fixed 3 A floor instead of the user's charge-detection setting, and no C10 range-extender AC charge
(pack current near 0, cable state 2, time left). Until the next poll, that row said "not charging" over
a running charge, or "charging" under a floor the user had raised.
"""
import pytest

import client
import db_reader
from test_plug_detection import _latest_charging, _latest_plug, _web_db


def _frame(amps, cable=1):
    return {"1010": 0, "1319": 0, "100003": 80.0, "1149": cable, "1177": 400, "1178": amps, "1200": 90}


@pytest.mark.parametrize("sig, floor", [
    (_frame(-16.0), 2.0),
    (_frame(-2.5), 2.0),
    (_frame(-4.0), 5.0),
    (_frame(0.1, cable=2), 2.0),
], ids=["a charge", "a slow charge, under 3 A", "under a floor the user raised", "a C10 charging AC"])
def test_the_web_stores_what_the_poller_reads(sig, floor, tmp_path, monkeypatch):
    path = _web_db(tmp_path, monkeypatch)
    db_reader.set_setting("charge_detect_min_a", str(floor))
    monkeypatch.setattr(client, "_CHARGE_CURRENT_MIN_A", floor)
    polled = client._parse_signal("VIN123", sig)
    db_reader.save_fresh_signals(sig)
    assert (_latest_charging(path), _latest_plug(path)) == (polled.charging_status, int(polled.plug_connected))
