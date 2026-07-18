"""Display-time unit conversion (units.py). The DB always stores metric; these lock the
math + the three-system behaviour, esp. the UK quirk: miles/mph but °C (not °F)."""
import units as U


def _sys(mp, s):
    mp.setattr(U, "get_unit_system", lambda: s)


def test_distance(monkeypatch):
    _sys(monkeypatch, "metric");      assert U.dist(100) == "100 km"
    _sys(monkeypatch, "imperial_uk"); assert U.dist(100) == "62.1 mi"
    _sys(monkeypatch, "imperial_us"); assert U.dist(100) == "62.1 mi"
    assert U.dist(None) == "—"
    _sys(monkeypatch, "metric");      assert U.dist(12345, 0) == "12345 km"


def test_speed(monkeypatch):
    _sys(monkeypatch, "metric");      assert U.speed(100) == "100 km/h"
    _sys(monkeypatch, "imperial_uk"); assert U.speed(100) == "62 mph"


def test_temperature_uk_keeps_celsius(monkeypatch):
    _sys(monkeypatch, "metric");      assert U.temp(20) == "20 °C"
    _sys(monkeypatch, "imperial_uk"); assert U.temp(20) == "20 °C"     # UK = Celsius
    _sys(monkeypatch, "imperial_us"); assert U.temp(20) == "68 °F"
    _sys(monkeypatch, "imperial_us"); assert U.temp(100) == "212 °F"


def test_pressure(monkeypatch):
    _sys(monkeypatch, "metric");      assert U.pressure(2.31) == "2.31 bar"
    _sys(monkeypatch, "imperial_us"); assert U.pressure(2.0) == "29 psi"


def test_elevation(monkeypatch):
    _sys(monkeypatch, "metric");      assert U.elev(100) == "100 m"
    _sys(monkeypatch, "imperial_uk"); assert U.elev(100) == "328 ft"
    _sys(monkeypatch, "imperial_us"); assert U.elev(100) == "328 ft"
    assert U.elev(None) == "—"


def test_efficiency(monkeypatch):
    _sys(monkeypatch, "metric");      assert U.efficiency(15) == "15 kWh/100km"
    _sys(monkeypatch, "imperial_uk"); assert U.efficiency(15) == "4.1 mi/kWh"
    assert U.efficiency(0) == "—"


def test_unit_labels_and_values(monkeypatch):
    _sys(monkeypatch, "imperial_us")
    assert (U.dist_unit(), U.speed_unit(), U.temp_unit(), U.pressure_unit(), U.eff_unit(), U.elev_unit()) == \
           ("mi", "mph", "°F", "psi", "mi/kWh", "ft")
    assert U.dist_val(100) == 62.1
    assert U.temp_val(0) == 32
    assert U.elev_val(100) == 328
    _sys(monkeypatch, "imperial_uk")
    assert U.temp_unit() == "°C" and U.dist_unit() == "mi" and U.elev_unit() == "ft"
    _sys(monkeypatch, "metric")
    assert U.elev_unit() == "m" and U.elev_val(100) == 100 and U.elev_val(None) is None


def test_unknown_system_falls_back_to_metric(monkeypatch):
    monkeypatch.setattr(U.db_reader, "get_setting", lambda *a, **k: "klingon")
    assert U.get_unit_system() == "metric"
