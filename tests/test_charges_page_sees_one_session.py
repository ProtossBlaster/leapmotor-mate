"""La pagina Ricariche legge una coppia unita come UNA ricarica.

Il difetto che questo previene ha un precedente fresco: sui viaggi la fusione c'era da mesi, ma il
riassunto era l'unico lettore che non la conosceva e stampava la metà del percorso (#247, uscito
pubblico). Qui si parte dal lettore principale — chi non sa della fusione mostra due righe dove
l'auto ha fatto una ricarica sola.
"""
import sqlite3

import db as poller_db
import db_reader
import pytest


def _setup(tmp_path, monkeypatch):
    path = str(tmp_path / "c.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V1')")
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _charge(path, cid, start, end, ssoc, esoc, kwh, cost=0.0):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
        "energy_added_kwh, cost, duration_min, charge_type) VALUES (?,?,?,?,?,?,?,?,?, 'AC')",
        (cid, 1, start, end, ssoc, esoc, kwh, cost, 10.0))
    con.commit(); con.close()


def _pair(path):
    """I due pezzi veri della beta #29."""
    _charge(path, 1, "2026-08-12T18:00:00+00:00", "2026-08-12T18:04:57+00:00", 47.4, 89.6, 7.9, 2.1)
    _charge(path, 2, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 89.7, 93.4, 0.7, 0.2)


def test_a_merged_pair_is_one_row_on_the_page(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    assert len(db_reader.get_charges()) == 2

    db_reader.merge_charges(1, 2)

    rows = db_reader.get_charges()
    assert [r["id"] for r in rows] == [1]
    assert rows[0]["is_merged"] is True and rows[0]["merged_count"] == 2


def test_the_row_on_the_page_carries_the_whole_session(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    db_reader.merge_charges(1, 2)

    row = db_reader.get_charges()[0]
    assert row["start_soc"] == 47.4 and row["end_soc"] == 93.4
    assert row["energy_added_kwh"] == pytest.approx(8.6)
    assert row["cost"] == pytest.approx(2.3)


def test_the_group_is_composed_before_the_clock_is_applied(tmp_path, monkeypatch):
    """L'ordine conta: la composizione lavora sugli ISO grezzi in UTC, la conversione al fuso
    dell'utente viene dopo. Invertirli darebbe una data giusta e un'ora sbagliata."""
    p = _setup(tmp_path, monkeypatch); _pair(p)
    db_reader.merge_charges(1, 2)

    row = db_reader.get_charges()[0]
    assert row["started_at"] == db_reader._local_iso("2026-08-12T18:00:00+00:00")
    assert row["ended_at"] == db_reader._local_iso("2026-08-12T18:38:39+00:00")


def test_splitting_the_group_puts_both_rows_back_on_the_page(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    db_reader.merge_charges(1, 2)
    assert [r["id"] for r in db_reader.get_charges()] == [1]        # il figlio è sparito dall'elenco

    db_reader.unmerge_charges(1)

    assert [r["id"] for r in db_reader.get_charges()] == [2, 1]     # più recente in cima


def test_the_limit_counts_charges_not_rows(tmp_path, monkeypatch):
    """Chiedere le ultime 2 ricariche deve dare 2 ricariche, non 2 pezzi di una."""
    p = _setup(tmp_path, monkeypatch); _pair(p)
    _charge(p, 3, "2026-08-10T09:00:00+00:00", "2026-08-10T10:00:00+00:00", 30.0, 60.0, 20.0)
    db_reader.merge_charges(1, 2)

    rows = db_reader.get_charges(limit=2)
    assert [r["id"] for r in rows] == [1, 3]


def test_a_lone_charge_is_untouched_by_all_of_this(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    _charge(p, 3, "2026-08-10T09:00:00+00:00", "2026-08-10T10:00:00+00:00", 30.0, 60.0, 20.0)

    row = db_reader.get_charges()[0]
    assert row["is_merged"] is False and row["merged_count"] == 1
    assert row["duration_min"] == 10.0          # la sua, non ricalcolata


# ── l'anteprima, che non deve scrivere niente ───────────────────────────────────
def test_the_preview_shows_the_charge_the_merge_would_make(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)

    g = db_reader.preview_merge_charges(1, 2)

    assert g["merged_count"] == 2
    assert g["energy_added_kwh"] == pytest.approx(8.6)
    assert g["start_soc"] == 47.4 and g["end_soc"] == 93.4


def test_the_preview_writes_nothing(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)

    db_reader.preview_merge_charges(1, 2)

    assert len(db_reader.get_charges()) == 2
    assert db_reader.get_charges()[0]["is_merged"] is False


def test_the_preview_of_a_missing_row_is_nothing_not_a_crash(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    assert db_reader.preview_merge_charges(1, 404) is None
