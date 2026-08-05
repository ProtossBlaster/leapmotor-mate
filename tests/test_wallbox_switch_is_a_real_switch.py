"""Turning the wallbox OFF has to turn it off everywhere (Silvio, 05/08/26).

The switch in Settings hid the wallbox page, the session-energy line and the chart overlay — and
stopped there. `ha_client.get_live()` never looked at it, and neither did the poller: its only gate
is `wallbox_energy_applies()`, which asks *where* the charge happened, never *whether the feature is
on*. So with the switch off and a mapping still saved, every poll kept reading the meter and writing
`ac_energy_kwh`.

That is not a display detail. `poller/db.py` bills a home charge on the meter whenever that column
is set (`billed_on_ac`), `_billed_kwh` shows it as the charge's energy, and the card prints
"🔌 wallbox (billed)". An owner who turned the feature off would keep being charged at a meter he
had switched away from, with a plug icon he did not ask for.

One door, not four: the gate belongs in `get_live()`, which is the only way wallbox data reaches
either process. Off means there is no wallbox data, for anybody.
"""
import ha_client
import pytest

STATES = [
    {"entity_id": "sensor.wb_energy", "state": "1234.5",
     "attributes": {"unit_of_measurement": "kWh", "friendly_name": "Wallbox energy"}},
    {"entity_id": "sensor.wb_power", "state": "7000",
     "attributes": {"unit_of_measurement": "W", "friendly_name": "Wallbox power"}},
]
MAPPING = {"power": "sensor.wb_power", "energy": "sensor.wb_energy"}


@pytest.fixture
def ha(monkeypatch):
    """A fully working, fully mapped wallbox — so the only thing under test is the switch."""
    monkeypatch.setattr(ha_client, "_creds", lambda: ("http://ha.local:8123", "tok"))
    monkeypatch.setattr(ha_client, "_request", lambda *a, **k: (200, STATES))
    monkeypatch.setattr(ha_client, "get_mapping", lambda: MAPPING)

    def switch(on: bool):
        monkeypatch.setattr(ha_client.db_reader, "get_setting",
                            lambda k, default="": ("1" if on else "0") if k == "wallbox_enabled"
                            else default)
    return switch


def test_with_the_switch_on_the_meter_is_read(ha):
    """The control: everything below is about the switch, not about a broken stub."""
    ha(True)
    live = ha_client.get_live()
    assert live["configured"] is True
    assert live["energy_kwh"] == 1234.5
    assert live["power_kw"] == 7.0


def test_with_the_switch_off_there_is_no_meter_reading(ha):
    """The one that matters. `energy_kwh` is what the poller stores as `ac_energy_kwh`, and what a
    home charge is then billed on."""
    ha(False)
    assert ha_client.get_live()["energy_kwh"] is None


def test_with_the_switch_off_nothing_else_leaks_either(ha):
    """Not just the energy: power, status and the rest come through the same call, and a live tile
    fed by a wallbox the owner switched off is the same defect wearing a different number."""
    ha(False)
    live = ha_client.get_live()
    assert live["configured"] is False
    for key in ("power_kw", "energy_kwh", "status", "max_current_a", "speed", "max_power"):
        assert live[key] is None, f"{key} still came through with the wallbox switched off"
    assert live["charging"] is False


def test_the_saved_mapping_is_not_erased_by_switching_off(ha):
    """Off means ignored, not forgotten: switching back on must not mean mapping the entities
    again. The gate reads the switch, it does not touch what was saved."""
    ha(False)
    ha_client.get_live()
    assert ha_client.get_mapping() == MAPPING


def test_switching_back_on_restores_it(ha):
    ha(False)
    assert ha_client.get_live()["energy_kwh"] is None
    ha(True)
    assert ha_client.get_live()["energy_kwh"] == 1234.5


def test_the_poller_reads_through_that_same_door():
    """The poller has no gate of its own — `_read_wallbox_energy` calls `get_live()` and takes
    `energy_kwh`. That is why the switch belongs there and not in four places."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "poller" / "recorder.py").read_text()
    body = src.split("def _read_wallbox_energy", 1)[1].split("\n    def ", 1)[0]
    assert 'ha_client.get_live().get("energy_kwh")' in body
