"""The BetaTester bundle must carry the tester's refuels — the fuel_purchases table a REEV cost
question is made of. beta #36 (@michapr): the "cost per 100 km" card read €2.66 (electricity only)
while August burned €39.59 of petrol. The bundle showed the trips (already priced) but NOT the
refuels, so we could not see WHY reev_fuel_summary recomputed the fuel cost as zero — a purchase
entered after the drive, scoped to another vehicle, or with no price? The rows that answer it were
never exported, and we were left reproducing the card by hand.

So export them. Two columns earn their place beyond the amounts: `vehicle_id` (the scoping check —
a refuel under a different car than the trips is exactly the kind of thing that zeroes a per-vehicle
sum) and `created_at` (entered-WHEN, against the drive dates, for the "priced after the fact"
question). `note` is omitted: it is free text a tester might drop a place-name into, and this table
carries no coordinates otherwise.
"""
import zipfile

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

_FUEL_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS fuel_purchases ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, ts TEXT NOT NULL, "
    "liters REAL NOT NULL, price_per_l REAL NOT NULL, total_cost REAL, "
    "fuel_before_pct REAL, note TEXT, created_at TEXT)"
)


def _bundle(tmp_path, monkeypatch):
    """The real export endpoint, decrypted back (encrypt_bundle stubbed to a pass-through)."""
    import asyncio
    import io

    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    c.execute(_FUEL_SCHEMA)
    # One refuel: 20 L, €38.60, entered on 25 Aug for a drive weeks earlier — vehicle_id=1, and a
    # note that must NOT leave the machine.
    c.execute("INSERT INTO fuel_purchases (id, vehicle_id, ts, liters, price_per_l, total_cost,"
              " fuel_before_pct, note, created_at) VALUES (1,1,'2026-08-14T18:00:00+00:00',20.0,"
              "1.93,38.6,10.0,'Q8 near my house','2026-08-25T18:00:00+00:00')")
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


def test_the_bundle_carries_a_fuel_purchases_file(tmp_path, monkeypatch):
    z = _bundle(tmp_path, monkeypatch)
    assert "fuel_purchases.csv" in z.namelist(), f"no fuel_purchases.csv: {z.namelist()}"


def test_it_carries_the_amounts_and_the_scope(tmp_path, monkeypatch):
    body = _bundle(tmp_path, monkeypatch).read("fuel_purchases.csv").decode()
    assert "vehicle_id" in body, "no vehicle_id column — the scoping check is the point"
    assert "created_at" in body, "no created_at column — entered-when answers the timing question"
    for needle in ("20.0", "1.93", "38.6"):          # litres, €/L, total cost
        assert needle in body, f"the refuel amount {needle!r} is missing"


def test_it_omits_the_free_text_note(tmp_path, monkeypatch):
    body = _bundle(tmp_path, monkeypatch).read("fuel_purchases.csv").decode()
    assert "Q8 near my house" not in body, "the note (a possible place-name) leaked"
