"""Map "concentration" feature: charges cluster into physical charging stations (by GPS,
~110 m grid) for the map bubbles, and the Charges page can be pre-filtered to just one
station via ?station=<key>. Runs on a tmp_path DB (poller schema), CI-safe."""
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
