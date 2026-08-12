"""Le rotte dell'unione delle ricariche, e ciò che la pagina deve ricevere per disegnarle.

Il bottone lo si offre solo dove ha senso — cioè sulla riga che ha davanti a sé una ricarica
abbastanza vicina — ma chi decide davvero è il server: la rotta rivalida tutti i cancelli, perché
un pulsante non è un permesso.
"""
import asyncio
import sqlite3
import types

import db as poller_db
import db_reader
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
import main


def _setup(tmp_path, monkeypatch):
    path = str(tmp_path / "c.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V1')")
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _charge(path, cid, start, end, ssoc, esoc, kwh=1.0):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
        "energy_added_kwh, duration_min, charge_type) VALUES (?,?,?,?,?,?,?,?, 'AC')",
        (cid, 1, start, end, ssoc, esoc, kwh, 10.0))
    con.commit(); con.close()


def _pair(path):
    _charge(path, 1, "2026-08-12T18:00:00+00:00", "2026-08-12T18:04:57+00:00", 47.4, 89.6, 7.9)
    _charge(path, 2, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 89.7, 93.4, 0.7)


def _req():
    """Le due rotte non leggono nulla dalla richiesta: firmano solo il contratto di FastAPI."""
    return types.SimpleNamespace(headers={})


def _ids():
    return [c["id"] for c in db_reader.get_charges()]


# ── le rotte ────────────────────────────────────────────────────────────────────
def test_the_merge_route_joins_the_two_rows(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)

    resp = asyncio.run(main.charges_merge(_req(), a=2, b=1))

    assert resp.status_code == 200
    assert resp.headers.get("HX-Refresh") == "true"
    assert _ids() == [1]


def test_the_unmerge_route_splits_them_again(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    asyncio.run(main.charges_merge(_req(), a=2, b=1))

    resp = asyncio.run(main.charges_unmerge(_req(), parent=1))

    assert resp.status_code == 200
    assert _ids() == [2, 1]


def test_a_refused_merge_answers_with_a_message_not_a_crash(tmp_path, monkeypatch):
    """Un cancello che scatta deve tornare indietro come una riga rossa nella pagina, non come
    un 500: l'utente ha cliccato una cosa lecita su dati che non lo permettono."""
    p = _setup(tmp_path, monkeypatch); _pair(p)

    resp = asyncio.run(main.charges_merge(_req(), a=1, b=404))

    assert resp.status_code == 200
    assert "⚠️" in resp.body.decode()
    assert _ids() == [2, 1]          # non è cambiato niente


# ── quello che la pagina riceve per disegnare il bottone ────────────────────────
def test_each_row_knows_the_charge_before_it(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)

    rows = {c["id"]: c for c in db_reader.get_charges()}

    assert rows[2]["prev_charge_id"] == 1      # la #2 può unirsi alla #1
    assert rows[1]["prev_charge_id"] is None   # sotto la #1 non c'è niente


def test_the_button_is_offered_only_where_the_pause_is_short(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    _charge(p, 3, "2026-08-10T09:00:00+00:00", "2026-08-10T10:00:00+00:00", 30.0, 60.0)

    rows = {c["id"]: c for c in db_reader.get_charges()}

    assert rows[2]["can_merge_prev"] is True    # 30 secondi di buco
    assert rows[1]["can_merge_prev"] is False   # due giorni dalla #3
    assert rows[3]["can_merge_prev"] is False   # non c'è niente prima


def test_the_offer_disappears_once_the_rows_are_joined(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _pair(p)
    db_reader.merge_charges(1, 2)

    rows = db_reader.get_charges()

    assert len(rows) == 1
    assert rows[0]["can_merge_prev"] is False
    assert rows[0]["is_merged"] is True


# ── le otto lingue ──────────────────────────────────────────────────────────────
def test_every_language_can_say_it(tmp_path):
    """Una chiave che esiste solo in inglese lascia la pagina metà tradotta senza che niente
    fallisca: è il buco che aveva tenuto il polacco in inglese per mesi."""
    import json
    import pathlib
    keys = {"charge_merge_btn", "charge_merge_help", "charge_merge_preview_title",
            "charge_merge_confirm", "charge_merge_failed", "charge_unmerge_btn",
            "charge_unmerge_confirm", "charge_merge_segments", "charge_merge_pause"}
    locales = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    files = sorted(locales.glob("*.json"))
    assert len(files) == 8, f"lingue attese 8, trovate {len(files)}"
    for f in files:
        have = set(json.loads(f.read_text())["translations"])
        assert not (keys - have), f"{f.name}: mancano {sorted(keys - have)}"
