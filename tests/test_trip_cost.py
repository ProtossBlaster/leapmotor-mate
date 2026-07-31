"""Trip cost must use the €/kWh the last charge was actually BILLED on (GitHub #51).

For a HOME wallbox charge the cost is billed on the AC energy the wallbox delivered
(charges.ac_energy_kwh), which is larger than the battery (DC/SoC) energy that reached
the pack (charges.energy_added_kwh). Deriving the trip's €/kWh as cost ÷ battery energy
overstated it by the charging losses (often ~2× when the charge ended near 100%), so
every trip on a wallbox install showed an inflated cost. The trip must divide by the
SAME basis compute_cost used: AC energy for HOME, battery energy otherwise.

Runs on a tmp_path DB (poller schema + db_reader pointed at it) — CI-safe."""
import db as D            # poller schema (creates trips/charges tables + migrations)
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(65.0)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _charge(pdb, cid, *, location_type, energy_added, cost, ac=None,
            ended="2026-06-11T08:00:00+00:00"):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type, cost)"
        " VALUES (?,1,?,?,40,52,?,?,?,?)",
        (cid, ended, ended, energy_added, ac, location_type, cost))
    pdb._conn.commit()


def _trip(pdb, tid, *, started="2026-06-12T13:20:00+00:00", dist=38.0, eff=21.6):
    # energy_kwh = eff * dist / 100 = 8.21 kWh (riri19's real numbers)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " start_soc, end_soc, efficiency_kwh_100km) VALUES (?,1,?,?,?,46.3,33.7,?)",
        (tid, started, started, dist, eff))
    pdb._conn.commit()


def test_home_wallbox_trip_rate_uses_battery_energy(tmp_path, monkeypatch):
    """16 kWh AC x 0.16 = 2.56 EUR, ma nel pacco ne sono arrivati 8.

    Un viaggio consuma l'energia CHE STA NEL PACCO, quindi il costo pagato va diviso per quella:
    2,56 / 8 = 0,32. Dividendo per i 16 del contatore (com'era fino al 31/07/26) gli 8 kWh persi
    nel caricatore — soldi spesi davvero — non finivano nel costo di nessun viaggio, e la somma
    dei costi restava sotto la bolletta. Scelta di Silvio, 31/07/26.

    ⚠️ Cambia solo la BASE con cui si prezza un viaggio. Quanti kWh mostra la scheda della
    ricarica, i totali di periodo e il €/kWh della pagina Ricariche restano quelli del contatore
    (`_billed_kwh`): la' la domanda e' «quanto ho pagato al muro», e la risposta e' un'altra.
    """
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, location_type="HOME", energy_added=8.0, ac=16.0, cost=2.56)
    _trip(pdb, 10)
    d = db_reader.get_trip_detail(10)
    assert d["energy_kwh"] == 8.21
    assert d["cost_per_kwh"] == 0.32                 # 2,56 / 8 (pacco), non 2,56/16 = 0,16
    assert d["cost"] == 2.63                         # 8,21 x 0,32


def test_public_charge_trip_rate_uses_battery_energy(tmp_path, monkeypatch):
    """Public/away charge isn't AC-billed → rate stays cost ÷ battery energy."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, location_type="DC", energy_added=20.0, ac=None, cost=12.0)   # 0.60 €/kWh
    _trip(pdb, 10)
    d = db_reader.get_trip_detail(10)
    assert d["cost_per_kwh"] == 0.6                  # 12 / 20 (battery)


def test_wallbox_ac_present_but_not_home_uses_battery(tmp_path, monkeypatch):
    """A wallbox may report AC energy even on a charge tagged non-HOME (public), but the
    cost was billed on the battery energy there — the trip rate must match that basis."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, location_type="AC", energy_added=10.0, ac=11.0, cost=4.0)    # 0.40 €/kWh
    _trip(pdb, 10)
    d = db_reader.get_trip_detail(10)
    assert d["cost_per_kwh"] == 0.4                  # 4.0 / 10 (battery), NOT 4.0/11
