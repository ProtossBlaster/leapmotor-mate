"""La manutenzione deve partire dall'auto selezionata, non dall'installazione (#253, rastrellata).

Trovato rastrellando 56 schermate con due auto disgiunte: con la T03 selezionata, la pagina
Manutenzione mostrava «Odometro alla consegna 7777» — il minimo della C10. È l'unica pagina che ha
perso, e nessuno l'aveva mai segnalata.

Due cause, una sopra l'altra:

  * `maint_baseline_date` / `maint_baseline_km` sono impostazioni **di installazione**: le scrivi su
    un'auto e valgono per l'altra;
  * il ripiego, quando non le hai scritte, è peggio — `SELECT MIN(recorded_at), MIN(odometer_km)
    FROM positions` **senza `vehicle_id`**: prende il primo giorno e il chilometraggio più basso di
    tutte le auto insieme.

I tagliandi FATTI erano già per auto (`WHERE vehicle_id=?`). Era l'inizio del servizio a non esserlo
— e da quello escono tutte le scadenze. → [[feedback-gate-a-feature-find-every-copy]]
"""
import pytest

A, B = "LFZAAA0000000001", "LFZBBB0000000002"


@pytest.fixture
def two_cars(tmp_path, monkeypatch):
    """La T03 vecchia (dal 2021, 11111 km) accanto alla C10 nuova (dal 2026, 7777 km)."""
    import db as D
    import db_reader
    import maintenance

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id,vin,car_type) VALUES (1,?,'T03')", (A,))
    c.execute("INSERT INTO vehicles (id,vin,car_type) VALUES (2,?,'C10')", (B,))
    c.execute("INSERT INTO positions (vehicle_id,recorded_at,soc,charging,speed_kmh,odometer_km)"
              " VALUES (1,'2021-03-11T07:00:00+00:00',50,0,0,11111)")
    c.execute("INSERT INTO positions (vehicle_id,recorded_at,soc,charging,speed_kmh,odometer_km)"
              " VALUES (2,'2026-07-22T07:00:00+00:00',50,0,0,7777)")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader, maintenance


def test_the_inferred_start_is_the_picked_cars_own(two_cars):
    """La C10 è entrata in servizio nel 2026 a 7777 km. La T03 nel 2021 a 11111. Non si mescolano."""
    db_reader, maintenance = two_cars

    db_reader.set_active_vehicle(A)
    date_a, km_a, _ = maintenance.get_baseline()
    db_reader.set_active_vehicle(B)
    date_b, km_b, _ = maintenance.get_baseline()

    assert km_a == 11111, f"la T03 parte da {km_a}, cioè dai km dell'altra"
    assert km_b == 7777, f"la C10 parte da {km_b}"
    assert date_a.startswith("2021") and date_b.startswith("2026"), (date_a, date_b)


def test_a_start_set_by_hand_belongs_to_one_car(two_cars):
    """Scriverlo sulla T03 non deve riscriverlo sulla C10: è la data di consegna di UNA macchina."""
    db_reader, maintenance = two_cars

    db_reader.set_active_vehicle(A)
    maintenance.set_baseline("2021-04-01", 12000)
    assert maintenance.get_baseline()[:2] == ("2021-04-01", 12000.0)

    db_reader.set_active_vehicle(B)
    d, km, explicit = maintenance.get_baseline()
    assert (d, km) != ("2021-04-01", 12000.0), "la C10 ha ereditato la consegna della T03"
    assert explicit is False and km == 7777


def test_one_car_keeps_reading_what_it_always_read(tmp_path, monkeypatch):
    """Ogni installazione con una macchina sola: il valore scritto prima di questa correzione sta
    nella chiave senza VIN e deve continuare a valere, o la data di consegna sparisce a chi ce
    l'ha."""
    import db as D
    import db_reader
    import maintenance

    path = str(tmp_path / "one.db")
    pdb = D.Database(path)
    pdb._conn.execute("INSERT INTO vehicles (id,vin,car_type) VALUES (1,?,'B10')", (A,))
    pdb._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES"
                      " ('maint_baseline_date','2024-01-15')")
    pdb._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES"
                      " ('maint_baseline_km','5000')")
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    assert maintenance.get_baseline() == ("2024-01-15", 5000.0, True)
