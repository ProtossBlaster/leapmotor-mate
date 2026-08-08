"""A/C Off over MQTT must not be skipped after a Quick Cool/Heat — GitHub #67 (Gr1m214).

The `climate_off` MQTT command is guarded so that pressing "A/C Off" when the A/C is already off
is a no-op (poller/main.py). That guard reads `service.last_climate_on`, which is only ever set
by a POLL (mqtt.py). The optimistic publish after a command updated the HA sensor but NOT that
field — so right after a Quick Cool (before the next poll) `last_climate_on` still held the old
"off" value and the following "A/C Off" was silently skipped. The fix syncs `last_climate_on`
with the optimistic state at publish time.

(`ac_switch operate=off` is confirmed on the B10; on other models the cloud may accept but ignore
it — a separate, model-level limit. This test covers the GUARD, which is what made the command
never fire at all.)
"""
import types
import importlib.util
import pathlib

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")


def _poller_main():
    """Load poller/main.py under its own name (it collides with web/main.py otherwise)."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _api():
    api = types.SimpleNamespace(calls=[])
    api.quick_cool = lambda vin: api.calls.append(("quick_cool", vin))
    api.quick_heat = lambda vin: api.calls.append(("quick_heat", vin))
    api.windshield_defrost = lambda vin: api.calls.append(("windshield_defrost", vin))
    api.ac_switch = lambda vin, params=None: api.calls.append(("ac_switch", vin, params))
    api._remote_control = lambda **kw: api.calls.append(("_remote_control", kw))
    return api


def _setup(tmp_path, last_climate_on):
    import db as D
    pm = _poller_main()
    api = _api()
    client = types.SimpleNamespace(_api=api)
    pubs = []
    # ⚠️ Per car. Skipping "A/C Off" on ANOTHER car's state is how a real off gets swallowed.
    state = {"VIN1": last_climate_on}
    service = types.SimpleNamespace(
        climate_on_for=lambda vin: state.get(vin),
        set_climate_on=lambda vin, v: state.__setitem__(vin, v),
        publish_state=lambda vin, k, v: pubs.append((vin, k, v)))
    service._state = state
    db = D.Database(str(tmp_path / "t.db"))
    return pm, api, client, service, db, pubs


def test_quick_cool_syncs_last_climate_on_true(tmp_path):
    pm, api, client, service, db, pubs = _setup(tmp_path, last_climate_on=False)
    pm._handle_mqtt_command(client, service, db, "VIN1", "climate_cool", None)
    assert ("quick_cool", "VIN1") in api.calls
    assert pubs == []                               # no optimistic publish — HA shows only the real polled state
    assert service._state["VIN1"] is True          # the #67 fix: in-memory guard reference still synced


def test_ac_off_after_quick_cool_actually_fires(tmp_path):
    # last poll said OFF (the stale value that used to skip the command), then Cool → Off.
    pm, api, client, service, db, pubs = _setup(tmp_path, last_climate_on=False)
    pm._handle_mqtt_command(client, service, db, "VIN1", "climate_cool", None)   # ON
    pm._handle_mqtt_command(client, service, db, "VIN1", "climate_off", None)    # then OFF
    assert any(c[0] == "ac_switch" and c[2] == {"operate": "off"} for c in api.calls)
    assert pubs == []                                # no optimistic publish
    assert service._state["VIN1"] is False          # A/C Off resets the in-memory guard reference


def test_ac_off_when_genuinely_off_is_still_a_noop(tmp_path):
    # Nothing turned it on first → the guard must still treat A/C Off as a no-op (no command sent).
    pm, api, client, service, db, pubs = _setup(tmp_path, last_climate_on=False)
    pm._handle_mqtt_command(client, service, db, "VIN1", "climate_off", None)
    assert not any(c[0] == "ac_switch" for c in api.calls)
    assert pubs == []
