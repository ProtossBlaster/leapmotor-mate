"""A C10 range extender on an AC charge keeps the pack current it reports, and the power from it.

Beta #13, @ebagnoli, 01/10/2026: Charge Current and Charge Power read `unknown` in Home Assistant
whenever his C10 range extender was plugged in, while another integration showed them. Mate 4.0.0
had brought in a quirk for exactly his car: the pack current (signal 1178) was taken to read ~0 A
through an AC charge, as it did on his frames in July, so with the cable in it was dropped, and the
power derived from it with it.

His bundle says the sensor measures now. In the eight days before 4.0.0 started dropping it —
19 to 26 September — Mate logged 1,373 polls of his car charging on AC with the current visible:
none under 2 A, from 2.6 to 18.8 A, the slow overnight charges at ~3 A included. The frame below
is his, taken at 13:44 during a home charge while Mate logged `A=—`: -16.299 A at 340.9 V, 5.6 kW
into the pack. Dropping it cost Home Assistant both sensors on every charge since 27 September,
and every one of his 9 charges since then a peak power of 0.0 kW.

Charge DETECTION on this car keeps its own rule (`client._is_charging`, the cable's own state):
that is about whether a session is open, not about what the sensor reads.
"""
import pathlib

import client
import db as D
import mqtt as M

# His frame of 01/10/2026 13:44 (local), from the bundle's raw signals — the ones that shape the
# parsed reading; GPS was never in the bundle.
_HIS_FRAME = {
    "1": 1790855095150, "1010": 0, "1149": 1, "1177": 340.9, "1178": -16.299, "1197": 0,
    "1200": 115, "1204": 52, "1318": 8695, "1319": 0.0, "100003": 51.6, "3235": 100.0,
    "3263": 47500, "47": 1, "1256": 1, "1257": 1, "1258": 0,
}
VIN = "LFZTESTC10REEV0001"


def _poller_main():
    """poller/main.py under its own name — a bare `import main` gets web/main.py."""
    import importlib.util
    import sys
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_c10_current", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["poller_main_c10_current"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_his_frame_is_a_range_extender_charging_on_ac():
    data = client._parse_signal(VIN, _HIS_FRAME)
    assert data.is_reev and data.plug_connected and not data.dc_gun_connected and not data.v2l_active
    assert data.charging_status > 0


def _one_poll(tmp_path, monkeypatch):
    """One real poll, as the cloud answered it; the reading the first reader receives."""
    PM = _poller_main()
    db = D.Database(str(tmp_path / "c10.db"))
    vid = db.ensure_vehicle(VIN, "C10")

    class _Vehicle:
        vin, car_type, year, abilities, is_shared = VIN, "C10", 2025, None, False

    class _Client:
        def get_status(self, vehicle=None):
            return client._parse_signal(VIN, _HIS_FRAME)

        def get_charge_schedule(self, *args):
            return None

    seen = []
    process = PM.Recorder.process
    monkeypatch.setattr(PM.Recorder, "process", lambda self, data: (seen.append(data), process(self, data)))
    PM._poll_vehicle(db, _Client(), PM.VehicleContext(db, _Vehicle(), vid), PM.AccountState())
    assert len(seen) == 1
    return seen[0]


def test_the_poll_hands_the_recorder_his_current_and_its_power(tmp_path, monkeypatch):
    """What the car measured, not a None in its place: the session's peak is read from it."""
    data = _one_poll(tmp_path, monkeypatch)
    assert data.charge_current_a == -16.299
    assert data.charge_power_kw == 5.556


class _Broker:
    def __init__(self):
        self.sent = {}

    def is_connected(self):
        return True

    def publish(self, topic, payload=None, retain=False, **kw):
        self.sent[topic.rsplit("/", 1)[-1]] = payload


def test_home_assistant_receives_both_figures_instead_of_unknown(tmp_path, monkeypatch):
    """The reading the poll produced, published as the bridge publishes it: an empty payload is
    what Home Assistant shows as `unknown`."""
    bridge = M.MqttService(broker="h", port=1883, discovery_enabled=False, get_setting=lambda *a, **k: "")
    bridge.client = _Broker()
    bridge._publish_sensors(_one_poll(tmp_path, monkeypatch))
    assert bridge.client.sent["charge_current"] == "-16.299"
    assert bridge.client.sent["charge_power"] == "5.556"
