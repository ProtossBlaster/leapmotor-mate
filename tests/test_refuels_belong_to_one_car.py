"""I rifornimenti sono dell'auto selezionata, non dell'account.

`fuel_purchases` porta il suo `vehicle_id` fin dall'inizio, e **ogni** lettore lo usa — la spesa
REEV, la mappa dei prezzi medi, il prezzo miscelato, la nota automatica. Uno no: proprio quello che
disegna la pagina Rifornimenti. `list_fuel_purchases` legge la tabella intera, e da lì si tirano
dietro l'errore anche i due calendari, che passano da lei.

Il guaio non è teorico: la pagina Rifornimenti somma due auto, la scheda «spesa» delle Statistiche
somma la sola auto scelta, e le stesse parole sopra i due numeri dicono la stessa cosa.
→ [[feedback-two-numbers-one-word]]

E c'è un secondo mezzo passo, più insidioso perché **scrive**: confermando un rifornimento rilevato,
`confirm_fuel_detected` legge la riga del rilevamento — che sa a quale auto appartiene — e poi chiama
`add_fuel_purchase`, che lo marchia con l'auto **selezionata nella barra**. Se stavi guardando l'auto
A e confermi il rilevamento dell'auto B, il litro finisce su A. E la riga del rilevamento viene
cancellata subito dopo, quindi **dopo non si sa più** di chi fosse: è un danno che nessuna
riparazione può disfare, e per questo la guardia va messa qui.
"""
import sqlite3

import pytest

import db_reader


A, B = 1, 2      # due REEV sullo stesso account


class _ConnessioneCheNonSiChiude(sqlite3.Connection):
    """Il codice di produzione chiude la connessione in `finally`; il test la riusa per tutta la
    prova. `close` su sqlite3.Connection è di sola lettura, quindi si sottoclassa invece di
    sostituirla."""

    def close(self):
        pass


@pytest.fixture
def due_auto(monkeypatch):
    con = sqlite3.connect(":memory:", factory=_ConnessioneCheNonSiChiude)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE fuel_purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "vehicle_id INTEGER, ts TEXT, liters REAL, price_per_l REAL, total_cost REAL, "
                "fuel_before_pct REAL, note TEXT, created_at TEXT)")
    con.execute("CREATE TABLE fuel_detected (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "vehicle_id INTEGER, ts TEXT NOT NULL, ts_from TEXT NOT NULL, liters REAL NOT NULL, "
                "fuel_before_pct REAL, fuel_after_pct REAL, status TEXT NOT NULL DEFAULT 'pending', "
                "created_at TEXT)")
    # A: 10 L a luglio · B: 20 L lo stesso mese
    con.execute("INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost) "
                "VALUES (?, '2026-07-10T10:00:00+00:00', 10.0, 1.50, 15.0)", (A,))
    con.execute("INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost) "
                "VALUES (?, '2026-07-20T10:00:00+00:00', 20.0, 1.80, 36.0)", (B,))
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "_conn_rw", lambda: con)
    monkeypatch.setattr(db_reader, "_ensure_fuel_purchases", lambda *_: None)
    monkeypatch.setattr(db_reader, "_ensure_fuel_detected", lambda *_: None)
    return con


def _selezione(monkeypatch, vid):
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: vid)
    monkeypatch.setattr(db_reader, "_selected_or_first", lambda *_: vid)


def test_the_list_shows_only_the_selected_car(due_auto, monkeypatch):
    _selezione(monkeypatch, A)
    righe = db_reader.list_fuel_purchases()
    assert [r["liters"] for r in righe] == [10.0], f"vede anche i litri dell'altra auto: {righe}"

    _selezione(monkeypatch, B)
    assert [r["liters"] for r in db_reader.list_fuel_purchases()] == [20.0]


def test_the_calendar_totals_only_the_selected_car(due_auto, monkeypatch):
    """Il calendario non legge la tabella: passa da `list_fuel_purchases`. Se la lista è a posto lo è
    anche lui — ed è proprio questo che va fissato, o la prossima correzione ne sistema uno solo."""
    _selezione(monkeypatch, A)
    luglio = db_reader.get_fuel_calendar_month(2026, 7)
    assert luglio["total"]["liters"] == 10.0, luglio["total"]
    assert luglio["total"]["cost"] == 15.0, luglio["total"]
    assert 20 not in luglio["days"], "il giorno dell'altra auto è comparso nella griglia"


def test_a_detected_refuel_is_filed_under_its_OWN_car(due_auto, monkeypatch):
    """La guardia sulla SCRITTURA. Guardo l'auto A e confermo un rilevamento dell'auto B: il litro
    deve restare di B. Sbagliare qui è irreparabile — la riga del rilevamento viene cancellata."""
    due_auto.execute(
        "INSERT INTO fuel_detected (vehicle_id, ts, ts_from, liters, fuel_before_pct, status) "
        "VALUES (?, '2026-08-01T09:00:00+00:00', '2026-08-01T08:00:00+00:00', 30.0, 5.0, 'pending')",
        (B,))
    due_auto.commit()
    det_id = due_auto.execute("SELECT id FROM fuel_detected").fetchone()["id"]

    _selezione(monkeypatch, A)                      # …ma nella barra c'è l'ALTRA auto
    pid = db_reader.confirm_fuel_detected(det_id, price_per_l=1.9)
    assert pid, "la conferma non ha creato il rifornimento"

    riga = due_auto.execute("SELECT vehicle_id, liters FROM fuel_purchases WHERE id = ?",
                            (pid,)).fetchone()
    assert riga["vehicle_id"] == B, (
        f"il rifornimento di B è finito sull'auto {riga['vehicle_id']} (la selezionata)")


def test_editing_and_deleting_cannot_reach_across_cars(due_auto, monkeypatch):
    """Difesa in profondità: dopo la correzione la pagina non mostra più le righe altrui, quindi
    l'id non si può nemmeno cliccare. Ma la rotta accetta un id qualsiasi, e una richiesta costruita
    a mano non deve poter toccare l'altra auto."""
    altrui = due_auto.execute("SELECT id FROM fuel_purchases WHERE vehicle_id = ?", (B,)).fetchone()["id"]
    _selezione(monkeypatch, A)

    assert db_reader.delete_fuel_purchase(altrui) is False, "ha cancellato il rifornimento dell'altra auto"
    assert due_auto.execute("SELECT COUNT(*) c FROM fuel_purchases WHERE vehicle_id = ?",
                            (B,)).fetchone()["c"] == 1

    assert db_reader.update_fuel_purchase(altrui, price_per_l=9.99) is False
    assert due_auto.execute("SELECT price_per_l FROM fuel_purchases WHERE id = ?",
                            (altrui,)).fetchone()["price_per_l"] == 1.80
