"""Le ricariche possono portare un marcatore di fusione, e i DB già esistenti lo guadagnano.

La fusione delle ricariche ricalca quella dei viaggi: una colonna sola, `merged_into_id`, e la
fusione scrive SOLO quella — nessun campo viene sovrascritto, quindi separare rimette esattamente
le righe di prima.

La migrazione vive nel POLLER, come tutte le altre. Il web serve lo stesso file e non lo altera
mai, quindi una lettura non può dare per scontato che la migrazione sia già passata:
→ tests/test_reads_survive_an_unmigrated_db.py tiene quella regola.
"""
import sqlite3

import schema


def _legacy_db(path) -> sqlite3.Connection:
    """Una tabella charges come stava PRIMA di questa versione: nessun merged_into_id."""
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE charges (
        id INTEGER PRIMARY KEY, vehicle_id INT, started_at TEXT, ended_at TEXT,
        start_soc REAL, end_soc REAL, energy_added_kwh REAL, duration_min REAL,
        latitude REAL, longitude REAL, charge_type TEXT, location_type TEXT,
        max_power_kw REAL, cost REAL, ac_energy_kwh REAL,
        wallbox_energy_start_kwh REAL, wb_stuck_kwh REAL, gross_kwh REAL, note TEXT)""")
    conn.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at) "
                 "VALUES (1, 1, '2026-08-12T18:00:00', '2026-08-12T18:04:57')")
    conn.commit()
    return conn


def _charge_columns(conn) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(charges)").fetchall()}


def test_an_existing_charges_table_gains_the_merge_column(tmp_path):
    conn = _legacy_db(tmp_path / "old.db")
    assert "merged_into_id" not in _charge_columns(conn)

    schema.ensure_schema(conn)

    assert "merged_into_id" in _charge_columns(conn)


def test_the_migration_does_not_touch_the_rows_it_finds(tmp_path):
    """Aggiungere la colonna non è un'occasione per riscrivere niente: la riga che c'era resta
    identica, e il marcatore nasce vuoto — nessuna ricarica è unita finché non lo decide l'utente."""
    conn = _legacy_db(tmp_path / "old.db")

    schema.ensure_schema(conn)

    row = conn.execute("SELECT started_at, ended_at, merged_into_id FROM charges WHERE id=1").fetchone()
    assert row[0] == "2026-08-12T18:00:00"
    assert row[1] == "2026-08-12T18:04:57"
    assert row[2] is None


def test_a_fresh_database_has_the_column_from_the_start(tmp_path):
    """Chi installa Mate oggi non passa da nessuna migrazione: la colonna sta nel CREATE TABLE."""
    conn = sqlite3.connect(str(tmp_path / "new.db"))

    schema.ensure_schema(conn)

    assert "merged_into_id" in _charge_columns(conn)
