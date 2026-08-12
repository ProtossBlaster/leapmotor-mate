"""Unire due ricariche — reversibile, e con dei cancelli che dicono quando NON si può.

L'auto dichiara il cavo assente nell'istante in cui la corrente si ferma, quindi un attacco solo
torna indietro come più righe. Unire è la risposta scelta perché non deve indovinare niente: è
l'utente a dire che quelle righe erano una sessione, e può disdirlo.

Ma proprio perché la fusione si fida dell'utente, i cancelli devono essere severi: due ricariche
lontane, o con in mezzo un'altra ricarica o della guida, NON erano la stessa sessione — e fonderle
sposterebbe energia e costo dentro una riga che non li ha fatti, con l'aria di essere tutto in
ordine (la fusione scrive solo il marcatore). È lo stesso incidente evitato sui viaggi con #186.
"""
import sqlite3

import db as poller_db
import db_reader


def _setup(tmp_path, monkeypatch):
    path = str(tmp_path / "c.db")
    poller_db.Database(path)                       # costruisce lo schema, merged_into_id incluso
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V1')")
    con.execute("INSERT INTO vehicles (id, vin) VALUES (2, 'V2')")
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _charge(path, cid, start, end, ssoc, esoc, kwh=1.0, vid=1):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
        "energy_added_kwh, duration_min, charge_type) VALUES (?,?,?,?,?,?,?,?, 'AC')",
        (cid, vid, start, end, ssoc, esoc, kwh, 10.0))
    con.commit(); con.close()


def _trip(path, tid, start, end, vid=1):
    con = sqlite3.connect(path)
    con.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km) "
                "VALUES (?,?,?,?, 12.0)", (tid, vid, start, end))
    con.commit(); con.close()


def _rows(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM charges ORDER BY id").fetchall()]
    con.close()
    return out


def _pair(path):
    """I due pezzi veri della beta #29: 30 secondi di buco, SoC che prosegue."""
    _charge(path, 1, "2026-08-12T18:00:00+00:00", "2026-08-12T18:04:57+00:00", 47.4, 89.6, 7.9)
    _charge(path, 2, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 89.7, 93.4, 0.7)


# ── quello che DEVE funzionare ──────────────────────────────────────────────────
def test_two_pieces_of_the_same_session_merge(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    res = db_reader.merge_charges(1, 2)
    assert res["ok"] is True and res["parent_id"] == 1


def test_the_earlier_row_becomes_the_parent_whatever_the_order(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    assert db_reader.merge_charges(2, 1)["parent_id"] == 1


def test_a_third_piece_joins_the_group_that_already_exists(tmp_path, monkeypatch):
    """Il caso vero del 29→30/07: una notte sola tornata come SEI righe. Si uniscono a catena,
    e ogni pezzo punta al genitore — non al pezzo precedente."""
    p = _setup(tmp_path, monkeypatch); _pair(p)
    _charge(p, 3, "2026-08-12T18:39:10+00:00", "2026-08-12T19:10:00+00:00", 93.5, 96.0, 0.5)
    db_reader.merge_charges(1, 2)
    assert db_reader.merge_charges(1, 3)["ok"] is True
    kids = db_reader._charge_children_by_parent(db_reader._get())
    assert sorted(c["id"] for c in kids[1]) == [2, 3]


def test_unmerge_restores_every_row_untouched(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    before = _rows(p)
    db_reader.merge_charges(1, 2)
    res = db_reader.unmerge_charges(1)
    assert res["restored"] == 1
    assert _rows(p) == before          # era cambiato SOLO il marcatore


# ── i cancelli ──────────────────────────────────────────────────────────────────
def test_two_cars_never_merge(tmp_path, monkeypatch):
    """#186 sui viaggi: due id e nessuno che controllasse fossero della stessa auto. Qui
    sposterebbe kWh ed euro dentro la ricarica di un'altra macchina."""
    p = _setup(tmp_path, monkeypatch); _pair(p)
    _charge(p, 9, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 30.0, 40.0, 5.0, vid=2)
    assert db_reader.merge_charges(1, 9)["error"] == "different_car"


def test_a_charge_still_running_cannot_be_merged(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    con = sqlite3.connect(p)
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc) "
                "VALUES (4, 1, '2026-08-12T18:05:27+00:00', NULL, 89.7)")
    con.commit(); con.close()
    assert db_reader.merge_charges(1, 4)["error"] == "not_found_or_already_merged"


def test_a_row_already_merged_cannot_be_merged_again(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    _charge(p, 3, "2026-08-12T18:39:10+00:00", "2026-08-12T19:10:00+00:00", 93.5, 96.0)
    db_reader.merge_charges(1, 2)
    assert db_reader.merge_charges(2, 3)["error"] == "not_found_or_already_merged"


def test_a_far_apart_pair_is_not_one_session(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    _charge(p, 1, "2026-08-12T14:00:00+00:00", "2026-08-12T14:10:00+00:00", 40.0, 50.0)
    _charge(p, 2, "2026-08-12T15:30:00+00:00", "2026-08-12T15:40:00+00:00", 50.0, 60.0)
    assert db_reader.merge_charges(1, 2)["error"] == "gap_too_large"


def test_another_charge_in_between_blocks_the_merge(tmp_path, monkeypatch):
    """Se fra le due c'è una terza ricarica, non erano contigue: unirle si mangerebbe quella."""
    p = _setup(tmp_path, monkeypatch)
    _charge(p, 1, "2026-08-12T10:00:00+00:00", "2026-08-12T10:10:00+00:00", 40.0, 50.0)
    _charge(p, 2, "2026-08-12T10:12:00+00:00", "2026-08-12T10:14:00+00:00", 50.0, 52.0)
    _charge(p, 3, "2026-08-12T10:20:00+00:00", "2026-08-12T10:30:00+00:00", 52.0, 60.0)
    assert db_reader.merge_charges(1, 3)["error"] == "charge_in_gap"


def test_a_trip_in_between_blocks_the_merge(tmp_path, monkeypatch):
    """L'auto è andata da qualche parte fra le due: non era una sessione sola, anche se il SoC
    per caso torna a combaciare."""
    p = _setup(tmp_path, monkeypatch)
    _charge(p, 1, "2026-08-12T12:00:00+00:00", "2026-08-12T12:10:00+00:00", 50.0, 60.0)
    _charge(p, 2, "2026-08-12T12:35:00+00:00", "2026-08-12T12:50:00+00:00", 60.0, 70.0)
    _trip(p, 1, "2026-08-12T12:15:00+00:00", "2026-08-12T12:30:00+00:00")
    assert db_reader.merge_charges(1, 2)["error"] == "drove_in_gap"


def test_a_soc_that_fell_in_between_blocks_the_merge(tmp_path, monkeypatch):
    """Lo specchio della regola dei viaggi: là una RISALITA di SoC vuol dire una ricarica nel
    buco; qui una DISCESA vuol dire che l'auto si è mossa, anche senza un viaggio registrato."""
    p = _setup(tmp_path, monkeypatch)
    _charge(p, 1, "2026-08-12T12:00:00+00:00", "2026-08-12T12:10:00+00:00", 50.0, 60.0)
    _charge(p, 2, "2026-08-12T12:35:00+00:00", "2026-08-12T12:50:00+00:00", 45.0, 70.0)
    assert db_reader.merge_charges(1, 2)["error"] == "drove_in_gap"


def test_the_tiny_soc_wobble_of_a_real_pause_does_not_block_it(tmp_path, monkeypatch):
    """Una pausa vera lascia il SoC dov'era, a meno di un decimo: quello non è aver guidato."""
    p = _setup(tmp_path, monkeypatch)
    _charge(p, 1, "2026-08-12T12:00:00+00:00", "2026-08-12T12:10:00+00:00", 50.0, 60.0)
    _charge(p, 2, "2026-08-12T12:12:00+00:00", "2026-08-12T12:50:00+00:00", 59.9, 70.0)
    assert db_reader.merge_charges(1, 2)["ok"] is True


def test_a_missing_id_is_refused_not_crashed(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    assert db_reader.merge_charges(1, 404)["error"] == "not_found_or_already_merged"
