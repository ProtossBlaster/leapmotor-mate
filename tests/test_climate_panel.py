"""Extended climate panel signals — validated on-car (B10, 2026-06-20) via the dump+diff +
fuzz + reverse-from-PAD method:

  • fan level   = 1941 (acAirVolume), range 1-7; 0 rejected by the car. HOLDS last level when off.
  • recirc      = 1943, BINARY: 1 = recirculate (circle:in) / 0 = fresh air (circle:out).
                  (Fuzzing 2/3/auto on the command + reverse from the PAD both confirm binary.)
  • base mode   = 3713: 0=auto · 1=cool · 3=heat · 4=vent (2 = unseen gap). 1939=1 only in auto.

The old SDK + kerniger MISLABEL 1941 as drive_status — the on-car diff proved it's the fan.
CI-safe (pure parse, no network)."""
import client


def test_fan_level_from_1941():
    assert client._parse_signal("V", {"1941": 1}).fan_level == 1
    assert client._parse_signal("V", {"1941": 4}).fan_level == 4
    assert client._parse_signal("V", {"1941": 7}).fan_level == 7
    assert client._parse_signal("V", {}).fan_level == 0          # signal absent → no data


def test_recirculation_from_1943():
    assert client._parse_signal("V", {"1943": 1}).recirculation is True    # recirc / circle:in
    assert client._parse_signal("V", {"1943": 0}).recirculation is False   # fresh  / circle:out
    assert client._parse_signal("V", {}).recirculation is False


def test_climate_mode_and_label_from_3713():
    for raw, label in {0: "auto", 1: "cool", 3: "heat", 4: "vent"}.items():
        d = client._parse_signal("V", {"3713": raw})
        assert d.climate_mode == raw
        assert d.climate_mode_label == label
    # missing → None / "" ; the unseen value 2 → no label (gap, not invented)
    nd = client._parse_signal("V", {})
    assert nd.climate_mode is None and nd.climate_mode_label == ""
    assert client._parse_signal("V", {"3713": 2}).climate_mode_label == ""


def test_climate_venting_only_from_real_vent_mode(tmp_path, monkeypatch):
    """Regression (bug reported 2026-06-21): plain A/C-on lands the car in AUTO (3713=0) — it must
    NOT show as "Ventilazione". `climate_venting` now derives from the REAL vent mode (3713==4)
    gated on A/C being on, NOT the old absence-of-cool/heat heuristic that lit up for AUTO."""
    import db as D            # poller schema + migrations
    import db_reader
    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.upsert_vehicle("VIN", "B10")

    def venting(ac_on, mode):                      # 1938 = acSwitch, 3713 = climate mode
        db_reader.save_fresh_signals({"1938": ac_on, "3713": mode})
        return db_reader.get_latest_status()["climate_venting"]

    assert venting(1, 0) is False    # A/C on, AUTO → NOT venting (the reported bug)
    assert venting(1, 1) is False    # A/C on, cool → NOT venting
    assert venting(1, 3) is False    # A/C on, heat → NOT venting
    assert venting(1, 4) is True     # A/C on, VENT → venting (the only true case)
    assert venting(0, 4) is False    # vent mode but A/C off (modes persist) → NOT venting
