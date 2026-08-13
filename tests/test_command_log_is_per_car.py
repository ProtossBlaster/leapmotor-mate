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


def test_logging_without_a_vin_never_breaks_the_command(tmp_path, monkeypatch):
    """Il registro è best-effort: se il VIN non c'è la riga si scrive comunque, senza auto —
    meglio una riga anonima che un comando che fallisce per colpa del suo diario."""
    _db(tmp_path, monkeypatch)

    db_reader.log_command("lock", "confirmed", 900)

    db_reader.set_active_vehicle("VIN_A")
    assert db_reader.command_responsiveness()["total"] == 0     # anonima → fuori dal conteggio
