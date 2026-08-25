"""The BetaTester bundle must carry what each PAGE computed for the same trips — beta #35 (@michapr):
the Trips page, the Statistics page and the Monthly Report print three different kWh/100km for one
month (10.1 / 12.2 / 9.6), and each is a different formula on the same rows:

  · Statistics  — Σ(distance × efficiency_kwh_100km) ÷ Σ(distance where efficiency is set), UTC month;
  · Trips       — getEC (ec_kwh) ÷ the km that HAVE a getEC reading, local month;
  · Report      — getEC ÷ ALL the km.

The Statistics and Trips figures diverge on the ENERGY COLUMN (efficiency vs getEC) and its km base,
not on a mean-of-ratios.

The raw trips are already in the bundle, but not what each page MADE of them — so the divergence had
to be reproduced by hand off screenshots. This dumps each page's OWN computed output per month, plus
the cost card's elec/fuel/total, so the three numbers and their inputs (energy, km, trip count) sit
side by side in the pack.
"""
import json
import zipfile

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


def _bundle(tmp_path, monkeypatch):
    import asyncio
    import io

    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    # Two July trips where efficiency (20) and getEC disagree: each drove 100 km on 10 kWh of getEC
    # (= 10 kWh/100km) but carries an efficiency of 20. So the Statistics page (efficiency) reads
    # 20.0 and the Trips page (getEC) reads 10.0 for the same July — the divergence, from the energy
    # COLUMN, side by side in the file.
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc,"
              " end_soc, ec_kwh, efficiency_kwh_100km) VALUES "
              "(1,1,'2026-07-10T08:00:00+00:00','2026-07-10T09:00:00+00:00',100,90,60,10.0,20.0)")
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc,"
              " end_soc, ec_kwh, efficiency_kwh_100km) VALUES "
              "(2,1,'2026-07-11T08:00:00+00:00','2026-07-11T09:00:00+00:00',100,60,40,10.0,20.0)")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    monkeypatch.setattr(main.research, "research_enabled", lambda: True)
    monkeypatch.setattr(main.command_client, "get_consumption_probe_raw", lambda: None)
    monkeypatch.setattr(main.command_client, "get_fresh_signals", lambda: {"1204": 88})
    monkeypatch.setattr(main.research, "encrypt_bundle", lambda b: b)

    resp = asyncio.run(main.research_export())
    return zipfile.ZipFile(io.BytesIO(resp.body))


def _page_stats(tmp_path, monkeypatch):
    return json.loads(_bundle(tmp_path, monkeypatch).read("page_stats.json").decode())


def test_the_bundle_carries_a_page_stats_file(tmp_path, monkeypatch):
    assert "page_stats.json" in _bundle(tmp_path, monkeypatch).namelist()


def test_it_carries_each_pages_own_output(tmp_path, monkeypatch):
    ps = _page_stats(tmp_path, monkeypatch)
    for key in ("statistics_page", "trips_page", "cost_card"):
        assert key in ps, f"page_stats is missing {key}: {list(ps)}"


def test_the_divergence_is_visible_side_by_side(tmp_path, monkeypatch):
    """The whole reason it exists: the Statistics figure (efficiency → 20) and the Trips getEC figure
    (10) both in the file for the same July, so no one has to reproduce a screenshot to see they
    disagree. Statistics is the page's real function (get_stats_grouped), nested year → months."""
    ps = _page_stats(tmp_path, monkeypatch)
    jul_stats = None
    for yr in ps["statistics_page"]:
        if "2026-07" in (yr.get("months") or {}):
            jul_stats = yr["months"]["2026-07"]
    assert jul_stats is not None, f"July not in statistics_page: {ps['statistics_page']}"
    assert jul_stats["avg_efficiency"] == 20.0, f"statistics efficiency figure not captured: {jul_stats}"
    jul_trips = ps["trips_page"]["2026-07"]
    assert jul_trips["kwh_100km"] == 10.0, f"trips getEC figure not captured: {jul_trips}"
