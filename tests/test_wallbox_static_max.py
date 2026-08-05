"""Static 'max available' fallback for the wallbox tile (#111, @Wartopia). When the wallbox exposes no
HA sensor for its max-available power, the user types a fixed value (kW or A) and the tile fills from it.
Display-only — never feeds cost. A mapped sensor always wins over the static value. No network (stubbed)."""
import ha_client
import db_reader


def _settings(monkeypatch, d):
    # `wallbox_enabled` unless a test says otherwise: since 05/08 `get_live()` honours the switch in
    # Settings, so a stub that leaves it out is describing a wallbox the owner switched OFF — and the
    # right answer there is no reading at all. Every test in this file is about a wallbox that is on;
    # what they check is which SOURCE its max-power figure comes from.
    d = {"wallbox_enabled": "1", **d}
    monkeypatch.setattr(db_reader, "get_setting", lambda k, default="": d.get(k, default))


# ── _static_max_power() ─────────────────────────────────────────────────────────────────────────────
def test_static_max_power_kw(monkeypatch):
    _settings(monkeypatch, {"wb_max_power_static": "11", "wb_max_power_unit": "kW"})
    assert ha_client._static_max_power() == (11.0, "kW")


def test_static_max_power_amps(monkeypatch):
    _settings(monkeypatch, {"wb_max_power_static": "16", "wb_max_power_unit": "A"})
    assert ha_client._static_max_power() == (16.0, "A")


def test_static_max_power_comma_decimal(monkeypatch):
    _settings(monkeypatch, {"wb_max_power_static": "7,4", "wb_max_power_unit": "kW"})
    assert ha_client._static_max_power() == (7.4, "kW")


def test_static_max_power_blank_is_none(monkeypatch):
    _settings(monkeypatch, {"wb_max_power_static": "  ", "wb_max_power_unit": "kW"})
    assert ha_client._static_max_power() == (None, "")


def test_static_max_power_nonnumeric_is_none(monkeypatch):
    _settings(monkeypatch, {"wb_max_power_static": "lots", "wb_max_power_unit": "kW"})
    assert ha_client._static_max_power() == (None, "")


def test_static_max_power_bad_unit_defaults_kw(monkeypatch):
    _settings(monkeypatch, {"wb_max_power_static": "11", "wb_max_power_unit": "W"})
    assert ha_client._static_max_power() == (11.0, "kW")


# ── get_live() fallback wiring ──────────────────────────────────────────────────────────────────────
def test_get_live_uses_static_when_no_sensor(monkeypatch):
    _settings(monkeypatch, {"wb_max_power_static": "11", "wb_max_power_unit": "kW"})
    monkeypatch.setattr(ha_client, "is_configured", lambda: True)
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {})            # nothing mapped
    monkeypatch.setattr(ha_client, "_request", lambda path: (200, []))   # no entities → no sensor value
    live = ha_client.get_live()
    assert live["max_power"] == 11.0 and live["max_power_unit"] == "kW"


def test_get_live_sensor_wins_over_static(monkeypatch):
    _settings(monkeypatch, {"wb_max_power_static": "11", "wb_max_power_unit": "kW"})
    monkeypatch.setattr(ha_client, "is_configured", lambda: True)
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {"max_power": "sensor.wb_max"})
    states = [{"entity_id": "sensor.wb_max", "state": "22.0",
               "attributes": {"unit_of_measurement": "kW"}}]
    monkeypatch.setattr(ha_client, "_request", lambda path: (200, states))
    live = ha_client.get_live()
    assert live["max_power"] == 22.0                                     # the sensor, not the static 11
