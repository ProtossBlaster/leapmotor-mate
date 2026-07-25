"""Map "concentration" feature: charges cluster into physical charging stations (by GPS,
~110 m grid) for the map bubbles, and the Charges page can be pre-filtered to just one
station via ?station=<key>. Runs on a tmp_path DB (poller schema), CI-safe."""
import asyncio

import pytest

import db as D
import db_reader


def _seed_charge(pdb, started, lat, lon, kwh=10.0, cost=None, name=None, location_type=None):
    pdb._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, latitude, longitude, cost, location_name, location_type)"
        " VALUES (1,?,?,30,80,?,?,?,?,?,?)",
        (started, started, kwh, lat, lon, cost, name, location_type))
    pdb._conn.commit()


def test_charging_stations_cluster_by_gps(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    # Three sessions at (near-)the same physical station — GPS fixes jitter slightly but
    # round to the same 3-decimal bucket — and one session far away.
    _seed_charge(pdb, "2026-01-01T10:00:00+01:00", 45.0700, 7.6860, kwh=10, cost=5, name="Ionity A")
    _seed_charge(pdb, "2026-02-01T10:00:00+01:00", 45.0701, 7.6861, kwh=20, cost=8, name="Ionity A")
    _seed_charge(pdb, "2026-03-01T10:00:00+01:00", 45.0699, 7.6859, kwh=15, cost=None, name=None)
    _seed_charge(pdb, "2026-04-01T10:00:00+01:00", 46.0000, 8.0000, kwh=5)

    stations = db_reader.get_charging_stations()
    assert len(stations) == 2

    station = next(s for s in stations if s["sessions"] == 3)
    assert station["name"] == "Ionity A"          # majority name wins over the unnamed session
    assert station["kwh"] == 45.0                 # 10 + 20 + 15
    assert station["cost"] == 13.0                # 5 + 8, the unpriced session excluded from the sum
    assert station["key"] == "45.070,7.686"
    assert len(station["recent"]) == 3
    assert station["recent"][0]["started_at"].startswith("2026-03-01")  # most recent first

    other = next(s for s in stations if s["sessions"] == 1)
    assert other["key"] == "46.000,8.000"


def test_charging_stations_min_sessions_and_top_n_cap(tmp_path, monkeypatch):
    """top_n bounds the marker count and the inline JSON blob (15, as get_frequent_places), but
    a station used ONCE still gets a marker — unlike a "frequent place", the charger you stopped
    at on one trip is exactly what this layer is for, and filtering singletons would leave a
    driver who charged at six different chargers on one holiday with an empty map."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    # 20 distinct one-off (single-session) stations, plus one with 2 sessions.
    for i in range(20):
        _seed_charge(pdb, f"2026-01-01T{i % 24:02d}:00:00+01:00", 40.0 + i, 8.0 + i, kwh=5)
    _seed_charge(pdb, "2026-02-01T10:00:00+01:00", 45.0700, 7.6860, kwh=10)
    _seed_charge(pdb, "2026-02-02T10:00:00+01:00", 45.0701, 7.6861, kwh=20)

    stations = db_reader.get_charging_stations()           # defaults: min_sessions=1, top_n=15
    assert len(stations) == 15                             # capped, not emptied
    assert stations[0]["sessions"] == 2                    # busiest station first…
    assert all(s["sessions"] == 1 for s in stations[1:])   # …then one-offs fill the cap

    uncapped = db_reader.get_charging_stations(top_n=None)
    assert len(uncapped) == 21                             # nothing dropped when uncapped

    # The filter still exists for a caller that wants only repeat stations.
    assert len(db_reader.get_charging_stations(min_sessions=2)) == 1


def test_charging_stations_excludes_home_by_learned_location(tmp_path, monkeypatch):
    """HOME exclusion must fire on a default install, where location_type is never set to HOME
    automatically (#153 review point 1) — it has to key off the wallbox location learned from
    real wallbox-energy charges (poller/db.py's _learned_wallbox_location), not location_type."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    # Two real home charges (ac_energy_kwh > 2) at the same spot, location_type never set —
    # learns the wallbox location without any user action.
    for started in ("2026-01-01T10:00:00+01:00", "2026-01-08T10:00:00+01:00"):
        pdb._conn.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
            " energy_added_kwh, ac_energy_kwh, latitude, longitude) VALUES (1,?,?,30,80,10,10,45.0700,7.6860)",
            (started, started))
    pdb._conn.commit()
    # A public charge 10m away from home (well inside the 1km radius) — must be excluded.
    _seed_charge(pdb, "2026-02-01T10:00:00+01:00", 45.0701, 7.6861, kwh=15)
    # A public charge far away — must show up as its own station.
    _seed_charge(pdb, "2026-03-01T10:00:00+01:00", 46.0000, 8.0000, kwh=5)

    stations = db_reader.get_charging_stations(min_sessions=1)
    assert len(stations) == 1
    assert stations[0]["key"] == "46.000,8.000"


def test_charges_grouped_filters_by_station_key(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    _seed_charge(pdb, "2026-01-01T10:00:00+01:00", 45.0700, 7.6860, kwh=10)
    _seed_charge(pdb, "2026-01-02T10:00:00+01:00", 45.0701, 7.6861, kwh=20)
    _seed_charge(pdb, "2026-01-03T10:00:00+01:00", 46.0000, 8.0000, kwh=5)

    stations = db_reader.get_charging_stations()
    key = next(s["key"] for s in stations if s["sessions"] == 2)

    grouped = db_reader.get_charges_grouped(station=key)
    n = sum(len(d["charges"]) for y in grouped for m in y["months"].values() for d in m["days"].values())
    assert n == 2

    assert db_reader.get_charges_grouped() != grouped  # unfiltered still returns all 3
    assert db_reader.get_charges_grouped(station="not,a-key") == []  # malformed key -> empty, not a crash


# ── Settings: user-adjustable marker threshold (min_sessions) ────────────────

class _Req:
    """Minimal stand-in for a Starlette Request — the endpoint only awaits .form()."""
    def __init__(self, data):
        self._data = data

    async def form(self):
        return self._data


def test_map_threshold_endpoint_saves_and_clamps(tmp_path, monkeypatch):
    """The Settings slider (1-10) persists as map_station_min_sessions. It defaults to 1 —
    showing every station, including the one-off charger on a holiday, which is what this map
    layer exists for; the slider is for the opposite taste, thinning a crowded map down to the
    spots the driver returns to. Out-of-range or garbage input is clamped/defaulted rather than
    stored verbatim (get_charging_stations would otherwise accept e.g. a negative min_sessions
    and filter out every station)."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")

    asyncio.run(main.save_map_station_threshold(_Req({"map_station_min_sessions": "1"})))
    assert db_reader.get_setting("map_station_min_sessions", "1") == "1"

    asyncio.run(main.save_map_station_threshold(_Req({"map_station_min_sessions": "5"})))
    assert db_reader.get_setting("map_station_min_sessions", "1") == "5"

    # Garbage falls back to the default — checked from 5, not from 1, so "fell back" can't be
    # confused with "was already there".
    asyncio.run(main.save_map_station_threshold(_Req({"map_station_min_sessions": "garbage"})))
    assert db_reader.get_setting("map_station_min_sessions", "9") == "1"

    asyncio.run(main.save_map_station_threshold(_Req({"map_station_min_sessions": "999"})))
    assert db_reader.get_setting("map_station_min_sessions", "1") == "10"   # clamped to max

    asyncio.run(main.save_map_station_threshold(_Req({"map_station_min_sessions": "0"})))
    assert db_reader.get_setting("map_station_min_sessions", "9") == "1"    # clamped to min
