"""Il backup dev'essere una FOTOGRAFIA, non una copia presa mentre qualcuno scrive.

`/api/export/database` faceva un `wal_checkpoint` e poi leggeva il file vivo a pezzi da 256 KB.
Il checkpoint mette a posto l'istante zero; il poller però continua a scrivere per tutto lo
scaricamento — che su un Pi con un database da centinaia di megabyte e una rete lenta dura minuti.
Se in mezzo cade un checkpoint (l'automatico del WAL a 1000 pagine, o il `VACUUM` della pulizia
quotidiana, che riscrive il file intero) le pagine cambiano **dietro** al lettore, e il .gz salvato
mescola due stati.

🔑 **Misurato, non dedotto**, e il risultato è più sottile di come l'avevo scritto: nella prova qui
sotto il file scaricato NON risulta corrotto — `integrity_check` dice «ok». Risulta **incoerente**.
Partito da 4000 righe, con 4000 aggiunte a metà scaricamento, il backup ne contiene **4005**: né lo
stato di prima né quello di dopo. Uno stato che nell'archivio non è mai esistito.
È questo che un backup non può essere, e su questo si misura — non su una corruzione che in questa
prova non si è prodotta. La corruzione resta possibile (il `VACUUM` riscrive le pagine sotto al
lettore) ma non l'ho riprodotta, e quindi non la affermo.
→ [[feedback-verified-vs-inferred]]

⚠️ La fotografia costa spazio: per un istante il database esiste in due copie. Se non ci sta, il
backup **non deve fallire** — meglio la copia imperfetta di prima che nessun backup. Provato sotto.
"""
import gzip
import os
import pathlib
import sqlite3

import pytest

import db_reader


def _riempi(con, righe, marca="vecchio"):
    con.executemany("INSERT INTO grosso (roba) VALUES (?)",
                    [(f"{marca}-{i}-{'x' * 400}",) for i in range(righe)])
    con.commit()


@pytest.fixture
def archivio(tmp_path, monkeypatch):
    """Un database abbastanza grande da servire parecchi pezzi: con un file che sta in un pezzo solo
    la lettura finisce prima che qualcuno possa scriverci, e il difetto non si manifesterebbe."""
    path = str(tmp_path / "grande.db")
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE grosso (id INTEGER PRIMARY KEY, roba TEXT)")
    _riempi(con, 4000)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()
    con.close()
    assert os.path.getsize(path) > 1_000_000, "il campione è troppo piccolo per essere significativo"
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _scarica_mentre_si_scrive(path, scrittura_dopo_pezzi=1):
    """Consuma il flusso e, dopo i primi pezzi, scrive nel database e forza un checkpoint — cioè
    esattamente ciò che fa il poller mentre l'utente sta scaricando."""
    pezzi = []
    flusso = db_reader.gzip_db_stream(chunk_size=64 * 1024)
    for n, pezzo in enumerate(flusso):
        pezzi.append(pezzo)
        if n == scrittura_dopo_pezzi:
            con = sqlite3.connect(path)
            _riempi(con, 4000, marca="nuovo")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # riscrive le pagine sotto al lettore
            con.execute("VACUUM")                            # e le rimescola tutte
            con.commit()
            con.close()
    return b"".join(pezzi)


def test_the_backup_is_one_instant_not_a_mixture(archivio, tmp_path):
    """Il difetto in una riga: un backup deve rappresentare UN istante.

    Si parte da 4000 righe e se ne aggiungono 4000 a scaricamento iniziato. Il file salvato deve
    contenerne 4000 — la fotografia scattata quando l'utente ha premuto Scarica. Senza fotografia ne
    contiene 4005: ha raccolto per strada un pezzo di scrittura altrui."""
    dati = gzip.decompress(_scarica_mentre_si_scrive(archivio))
    fuori = tmp_path / "scaricato.db"
    fuori.write_bytes(dati)

    con = sqlite3.connect(str(fuori))
    try:
        esito = con.execute("PRAGMA integrity_check").fetchone()[0]
        assert esito == "ok", f"il backup scaricato è corrotto: {esito}"
        n = con.execute("SELECT COUNT(*) FROM grosso").fetchone()[0]
        assert n == 4000, (
            f"il backup contiene {n} righe: non è lo stato di partenza (4000) né quello di arrivo "
            f"(8000), ma un miscuglio dei due — un istante che non è mai esistito")
    finally:
        con.close()


def test_the_snapshot_leaves_no_temporary_file_behind(archivio):
    """La fotografia sta su un file temporaneo accanto al database: dev'essere sparito alla fine,
    anche perché sul volume dei dati lo spazio è quello che è."""
    prima = set(os.listdir(os.path.dirname(archivio)))
    list(db_reader.gzip_db_stream(chunk_size=64 * 1024))
    dopo = set(os.listdir(os.path.dirname(archivio)))
    residui = {f for f in dopo - prima if not f.endswith(("-wal", "-shm"))}
    assert not residui, f"lasciati indietro: {residui}"


def test_an_aborted_download_still_cleans_up(archivio):
    """Se l'utente chiude il browser a metà, il generatore viene chiuso: il temporaneo va via lo
    stesso. Senza questo, ogni scaricamento interrotto lascerebbe una copia del database sul disco."""
    prima = set(os.listdir(os.path.dirname(archivio)))
    flusso = db_reader.gzip_db_stream(chunk_size=64 * 1024)
    next(flusso)
    flusso.close()
    dopo = set(os.listdir(os.path.dirname(archivio)))
    residui = {f for f in dopo - prima if not f.endswith(("-wal", "-shm"))}
    assert not residui, f"scaricamento interrotto, lasciati indietro: {residui}"


def test_if_the_snapshot_cannot_be_taken_the_backup_still_works(archivio, monkeypatch, caplog):
    """Lo spazio su disco può non bastare — la fotografia raddoppia il database per un istante. In
    quel caso si torna al comportamento di prima: una copia imperfetta è meglio di nessun backup.

    ⚠️ Questo è il ramo che rende la correzione SICURA da rilasciare: nessun utente resta senza
    esportazione perché la nuova strada non è percorribile sul suo dispositivo."""
    def _niente_spazio(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db_reader, "_db_snapshot", _niente_spazio)
    dati = gzip.decompress(b"".join(db_reader.gzip_db_stream(chunk_size=64 * 1024)))
    assert dati[:16].startswith(b"SQLite format 3"), "il ripiego non ha prodotto un database"


def test_the_explicit_path_argument_still_wins(archivio, tmp_path):
    """`gzip_db_stream(path)` con un percorso esplicito comprime QUEL file e basta — lo usa il
    ripristino nei test e non deve mettersi a fotografare niente."""
    altro = tmp_path / "altro.db"
    con = sqlite3.connect(str(altro))
    con.execute("CREATE TABLE t (a)")
    con.execute("INSERT INTO t VALUES ('segno-riconoscibile')")
    con.commit()
    con.close()
    dati = gzip.decompress(b"".join(db_reader.gzip_db_stream(path=str(altro))))
    assert b"segno-riconoscibile" in dati
    assert pathlib.Path(altro).read_bytes() == dati, "il file indicato non è stato reso tale e quale"
