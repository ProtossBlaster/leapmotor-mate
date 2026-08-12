"""Due righe di ricarica lette come una sessione sola — matematica di sola visualizzazione.

Le righe salvate non si toccano mai: la riga combinata la compone il lettore a ogni lettura, e
separare rimette esattamente quello che c'era. Qui si fissano le regole di composizione, una per
test, così che cambiarne una faccia diventare rosso il test che la nomina.

⚠️ La DURATA è la scelta ambigua di questo lavoro, ed è dichiarata: `fine − inizio`, quindi la
pausa dentro il gruppo ci finisce. È l'opposto dei viaggi, dove `duration_min` è la SOMMA dei
pezzi perché lì conta solo il tempo di guida. Per una ricarica conta invece la finestra che
l'utente vede sulla pagina («dalle 18:00 alle 18:38»).
"""
import sqlite3

import db as poller_db
import db_reader
import pytest

# I due pezzi veri della beta #29 (@michapr, B10 REEV, 12/08/2026): un fotogramma solo di «cavo
# assente» alle 18:04:56 ha chiuso la prima e aperto la seconda 30 secondi dopo.
A = {"id": 1, "vehicle_id": 1, "started_at": "2026-08-12T18:00:00", "ended_at": "2026-08-12T18:04:57",
     "start_soc": 47.4, "end_soc": 89.6, "energy_added_kwh": 7.9, "cost": 2.1,
     "ac_energy_kwh": 8.6, "gross_kwh": None, "wb_stuck_kwh": None,
     "max_power_kw": 1.6, "charge_type": "AC", "duration_min": 309.0, "note": "casa"}
B = {"id": 2, "vehicle_id": 1, "started_at": "2026-08-12T18:05:27", "ended_at": "2026-08-12T18:38:39",
     "start_soc": 89.7, "end_soc": 93.4, "energy_added_kwh": 0.7, "cost": 0.2,
     "ac_energy_kwh": 0.8, "gross_kwh": None, "wb_stuck_kwh": None,
     "max_power_kw": 1.7, "charge_type": "AC", "duration_min": 33.0, "note": None}


def test_the_group_spans_from_the_first_start_to_the_last_end():
    g = db_reader._charge_group_stats(A, [B])
    assert g["started_at"] == "2026-08-12T18:00:00"
    assert g["ended_at"] == "2026-08-12T18:38:39"
    assert g["merged_count"] == 2
    assert g["is_merged"] is True


def test_energy_cost_and_meter_are_summed():
    g = db_reader._charge_group_stats(A, [B])
    assert g["energy_added_kwh"] == pytest.approx(8.6)
    assert g["cost"] == pytest.approx(2.3)
    assert g["ac_energy_kwh"] == pytest.approx(9.4)


def test_soc_comes_from_the_two_ends_not_from_the_pieces():
    """Il ΔSoC del gruppo è quello vero della sessione: 47,4 → 93,4. È il numero da cui il SoH
    ricava la capacità, ed è il motivo per cui una ricarica spezzata gli mente."""
    g = db_reader._charge_group_stats(A, [B])
    assert g["start_soc"] == 47.4
    assert g["end_soc"] == 93.4


def test_peak_power_is_the_maximum_of_the_pieces():
    assert db_reader._charge_group_stats(A, [B])["max_power_kw"] == 1.7


def test_duration_is_end_minus_start_and_therefore_includes_the_pause():
    """Scelta dichiarata: 18:00 → 18:38:39 = 38,65 minuti, NON 309 + 33.
    Se un giorno si cambia idea, si cambia in `_charge_group_stats` e questo test lo dice."""
    g = db_reader._charge_group_stats(A, [B])
    assert g["duration_min"] == pytest.approx(38.65, abs=0.01)


def test_a_figure_nobody_reported_stays_missing():
    """Somma di niente è NIENTE, non zero: `gross_kwh` lo digita il proprietario, e se non l'ha
    digitato su nessuno dei due pezzi il gruppo non deve inventarsi uno 0 credibile."""
    g = db_reader._charge_group_stats(A, [B])
    assert g["gross_kwh"] is None
    g2 = db_reader._charge_group_stats(A, [dict(B, gross_kwh=1.2)])
    assert g2["gross_kwh"] == pytest.approx(1.2)


def test_the_type_follows_the_piece_that_carried_more_energy():
    """Una sosta in continua dentro una notte in alternata non deve far leggere 'AC' un gruppo
    che è stato soprattutto DC — e viceversa."""
    b_dc = dict(B, charge_type="DC", energy_added_kwh=40.0)
    assert db_reader._charge_group_stats(A, [b_dc])["charge_type"] == "DC"


def test_the_parent_keeps_place_and_note_and_the_child_keeps_its_own():
    g = db_reader._charge_group_stats(A, [B])
    assert g["note"] == "casa"
    assert B["note"] is None          # il figlio non è stato toccato


def test_a_lone_charge_is_not_a_group_and_keeps_its_own_duration():
    g = db_reader._charge_group_stats(A, [])
    assert g["merged_count"] == 1
    assert g["is_merged"] is False
    assert g["duration_min"] == 309.0


def test_the_pieces_are_ordered_by_time_not_by_the_order_they_arrive():
    """Il chiamante può passare i figli in qualunque ordine: il gruppo si ordina da sé."""
    g = db_reader._charge_group_stats(B, [A])
    assert g["started_at"] == "2026-08-12T18:00:00"
    assert g["ended_at"] == "2026-08-12T18:38:39"
    assert g["start_soc"] == 47.4


def test_composing_a_group_never_touches_the_rows_it_was_given():
    before_a, before_b = dict(A), dict(B)
    db_reader._charge_group_stats(A, [B])
    assert A == before_a and B == before_b


# ── il raggruppamento, su un DB vero ────────────────────────────────────────────
def _setup(tmp_path, monkeypatch):
    path = str(tmp_path / "c.db")
    poller_db.Database(path)                       # costruisce lo schema, merged_into_id incluso
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V')")
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, merged_into_id) "
                "VALUES (1, 1, '2026-08-12T18:00:00', '2026-08-12T18:04:57', NULL)")
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, merged_into_id) "
                "VALUES (2, 1, '2026-08-12T18:05:27', '2026-08-12T18:38:39', 1)")
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, merged_into_id) "
                "VALUES (3, 1, '2026-08-11T09:00:00', '2026-08-11T10:00:00', NULL)")
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def test_children_are_grouped_under_their_parent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    kids = db_reader._charge_children_by_parent(db_reader._get())
    assert list(kids) == [1]
    assert [c["id"] for c in kids[1]] == [2]


def test_a_charge_that_was_never_merged_appears_nowhere(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    kids = db_reader._charge_children_by_parent(db_reader._get())
    assert 3 not in kids
