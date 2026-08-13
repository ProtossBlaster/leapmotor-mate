"""Il registro dei comandi sa a quale auto si riferisce.

Il badge «quanto risponde l'auto» è un indizio di copertura cellulare: conta quanti comandi sono
stati confermati e quanti sono andati in timeout. Con due macchine era un numero solo — i timeout
della T03 in garage abbassavano il voto della B10 parcheggiata fuori, e viceversa.

🔴 Non era una query da filtrare: nel `command_log` la colonna del veicolo **non esisteva**, quindi
il dato non veniva proprio registrato. È la sesta forma della lista dell'audit.

⚠️ Le righe scritte prima non hanno un'auto e **non è deducibile quale fosse**: restano senza, e
vengono escluse dal conteggio invece di essere attribuite a qualcuno per comodità.
→ [[signal-absent-is-not-signal-zero]]
"""
import sqlite3

import db as poller_db
import db_reader


def _db(tmp_path, monkeypatch, vins=("VIN_A", "VIN_B")):
    path = str(tmp_path / "c.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    for i, vin in enumerate(vins, start=1):
        con.execute("INSERT INTO vehicles (id, vin) VALUES (?, ?)", (i, vin))
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _log(n, outcome, vin):
    for _ in range(n):
        db_reader.log_command("lock", outcome, 900, vin=vin)


def _log_of_an_older_mate(path, n, outcome="confirmed"):
    """Il registro com'era PRIMA della v3.13.0: la colonna dell'auto non esiste proprio.

    È l'unico modo onesto di provare un AGGIORNAMENTO: la colonna nasce da un `ALTER TABLE`, e ciò
    che decide il destino delle righe vecchie è quel momento lì — non un `vin` messo a NULL a mano
    su una tabella già nuova."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE command_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
                " action TEXT, outcome TEXT NOT NULL, latency_ms INTEGER)")
    for _ in range(n):
        con.execute("INSERT INTO command_log (ts, action, outcome, latency_ms) "
                    "VALUES ('2026-08-01T00:00:00', 'lock', ?, 800)", (outcome,))
    con.commit(); con.close()


def test_after_the_update_one_car_keeps_the_badge_it_had(tmp_path, monkeypatch):
    """🔴 Il difetto che Silvio ha visto il 13/08 sera: aggiornato Mate, il badge diventa «⚪ —».

    Con DUE auto escludere le righe senza VIN è giusto — di chi fossero non lo sa nessuno. Con UNA
    non c'è niente da indovinare: sono per forza sue, e novanta giorni di storico se ne vanno in un
    colpo solo. È la stessa forma delle altre tre migrazioni della v3.13.0 (il valore condiviso
    resta valido finché l'auto è una), ed è l'unico punto dove non era stata applicata."""
    path = _db(tmp_path, monkeypatch, vins=("VIN_A",))
    _log_of_an_older_mate(path, 10)

    r = db_reader.command_responsiveness()

    assert r["total"] == 10
    assert r["state"] == "responsive"


def test_a_mate_that_already_took_the_3_13_0_recovers_its_history_too(tmp_path, monkeypatch):
    """🔴 Il buco della correzione qui sopra, trovato preparando il container per Silvio.

    Recuperare le righe **nel momento in cui la colonna nasce** aiuta solo chi arriva dalla 3.12.0.
    Chi la 3.13.0 ce l'ha già — Silvio stesso, la sera del 13/08 — la colonna ce l'ha da ieri: quel
    momento è passato, non torna, e il trattino gli resterebbe per sempre. Il recupero non può
    appendersi all'`ALTER`: deve guardare lo **stato** (righe senza auto + una macchina sola)."""
    path = _db(tmp_path, monkeypatch, vins=("VIN_A",))
    con = sqlite3.connect(path)                       # lo stato in cui la 3.13.0 lo ha già lasciato:
    con.execute("CREATE TABLE command_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
                " action TEXT, outcome TEXT NOT NULL, latency_ms INTEGER, vin TEXT)")
    for _ in range(10):                               # la colonna c'è, le righe sono già anonime
        con.execute("INSERT INTO command_log (ts, action, outcome, latency_ms, vin) "
                    "VALUES ('2026-08-01T00:00:00', 'lock', 'confirmed', 800, NULL)")
    con.commit(); con.close()

    r = db_reader.command_responsiveness()

    assert r["total"] == 10
    assert r["state"] == "responsive"


def test_the_recovered_history_survives_the_next_page_load(tmp_path, monkeypatch):
    """🔴 Trappola presa in flagrante mentre si correggeva il difetto qui sopra.

    `_conn_rw()` apre una connessione NUOVA a ogni chiamata e non la chiude mai. L'`ALTER` va a
    segno da solo (è DDL, fuori transazione), ma l'`UPDATE` che timbra le righe apre una
    transazione: **senza `commit()` viene annullata** quando la connessione muore. Il badge
    risultava giusto una volta sola — era la stessa connessione a vedere la scrittura in sospeso —
    e dal caricamento dopo la colonna c'è già, il recupero non riparte più e il trattino torna per
    sempre. Un test che chiede il badge una volta sola **non lo vede**: chiederlo due volte è quello
    che fa qualunque utente, ricaricare la pagina."""
    path = _db(tmp_path, monkeypatch, vins=("VIN_A",))
    _log_of_an_older_mate(path, 10)

    db_reader.command_responsiveness()          # 1° caricamento: qui dentro scatta la migrazione
    r = db_reader.command_responsiveness()      # 2°: connessione nuova, deve trovare il lavoro fatto

    assert r["total"] == 10
    con = sqlite3.connect(path)
    left_anonymous = con.execute("SELECT COUNT(*) FROM command_log WHERE vin IS NULL").fetchone()[0]
    con.close()
    assert left_anonymous == 0


def test_with_two_cars_the_update_gives_the_old_rows_to_neither(tmp_path, monkeypatch):
    """La guardia della correzione qui sopra: recuperare lo storico vale **solo** se l'auto è una.

    Con due macchine quelle righe sono di chi non si sa, e assegnarle alla prima che capita — la più
    vecchia, la selezionata, una qualunque — vorrebbe dire giudicare la copertura di una col diario
    dell'altra: il difetto che la v3.13.0 ha appena chiuso, rimesso dentro dalla porta di servizio."""
    path = _db(tmp_path, monkeypatch)
    _log_of_an_older_mate(path, 10)

    db_reader.set_active_vehicle("VIN_A")
    assert db_reader.command_responsiveness()["total"] == 0
    db_reader.set_active_vehicle("VIN_B")
    assert db_reader.command_responsiveness()["total"] == 0


def test_each_car_is_judged_on_its_own_commands(tmp_path, monkeypatch):
    """Il difetto, in una prova sola: una macchina che risponde sempre e una che non risponde mai.
    Con un registro unico il voto era uno; separandoli, ognuna ha il suo."""
    _db(tmp_path, monkeypatch)
    _log(6, "confirmed", "VIN_A")
    _log(6, "timeout_car", "VIN_B")

    db_reader.set_active_vehicle("VIN_A")
    a = db_reader.command_responsiveness()
    db_reader.set_active_vehicle("VIN_B")
    b = db_reader.command_responsiveness()

    assert a["confirmed"] == 6 and a["timeouts"] == 0
    assert b["confirmed"] == 0 and b["timeouts"] == 6
    assert a["rate"] != b["rate"]


def test_the_other_cars_commands_do_not_count(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    _log(4, "confirmed", "VIN_A")
    _log(4, "timeout_car", "VIN_B")

    db_reader.set_active_vehicle("VIN_A")
    assert db_reader.command_responsiveness()["total"] == 4     # non 8


def test_rows_written_before_the_column_existed_are_left_out(tmp_path, monkeypatch):
    """Non si attribuiscono: a quale auto appartenessero non lo sa nessuno, e inventarlo
    sposterebbe il voto di una macchina con i comandi di un'altra."""
    path = _db(tmp_path, monkeypatch)
    _log(3, "confirmed", "VIN_A")
    con = sqlite3.connect(path)
    for _ in range(5):
        con.execute("INSERT INTO command_log (ts, action, outcome, latency_ms) "
                    "VALUES ('2026-01-01T00:00:00', 'lock', 'timeout_car', 800)")
    con.commit(); con.close()

    db_reader.set_active_vehicle("VIN_A")
    r = db_reader.command_responsiveness()

    assert r["total"] == 3 and r["timeouts"] == 0


def test_a_single_car_install_still_gets_its_badge(tmp_path, monkeypatch):
    """Chi ha una macchina sola non deve perdere il badge: i suoi comandi nuovi portano il VIN."""
    _db(tmp_path, monkeypatch, vins=("VIN_A",))
    _log(5, "confirmed", "VIN_A")

    r = db_reader.command_responsiveness()

    assert r["total"] == 5 and r["state"] != "unknown"


def test_a_database_without_a_vehicles_table_still_records_the_command(tmp_path, monkeypatch):
    """Il recupero dello storico chiede quante auto ci sono, e quella domanda può fallire: su uno
    schema minimo la tabella `vehicles` non esiste. Se l'eccezione esce dalla migrazione si porta
    dietro la scrittura della riga — il diario che rompe il comando, proprio ciò che `log_command`
    promette di non fare mai. La migrazione può saltare; il comando no."""
    path = str(tmp_path / "minimo.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE command_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
                " action TEXT, outcome TEXT NOT NULL, latency_ms INTEGER)")
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    db_reader.log_command("lock", "confirmed", 900, vin="VIN_X")

    con = sqlite3.connect(path)
    written = con.execute("SELECT COUNT(*) FROM command_log").fetchone()[0]
    con.close()
    assert written == 1


def test_logging_without_a_vin_never_breaks_the_command(tmp_path, monkeypatch):
    """Il registro è best-effort: se il VIN non c'è la riga si scrive comunque, senza auto —
    meglio una riga anonima che un comando che fallisce per colpa del suo diario."""
    _db(tmp_path, monkeypatch)

    db_reader.log_command("lock", "confirmed", 900)

    db_reader.set_active_vehicle("VIN_A")
    assert db_reader.command_responsiveness()["total"] == 0     # anonima → fuori dal conteggio
