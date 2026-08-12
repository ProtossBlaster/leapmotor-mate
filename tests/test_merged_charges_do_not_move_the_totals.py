"""Unire due ricariche non sposta nessun totale.

È vero per costruzione — i pezzi sommano al gruppo — ma «per costruzione» è esattamente il tipo di
affermazione che va misurata: basta che un lettore escluda i figli da una SOMMA per far sparire la
loro energia, ed è un difetto silenzioso, perché il numero resta plausibile.

⚠️ Questi sono test di INVARIANZA: nascono verdi di proposito. Perché non siano verdi di niente,
l'ultimo mostra il modo sbagliato di farlo e verifica che dia un numero DIVERSO — se un giorno
qualcuno escluderà i figli da una somma, la differenza che quel test misura sarà il difetto.
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


def _split_pair(path):
    con = sqlite3.connect(path)
    for cid, s, e, ss, es, kwh, cost in (
        (1, "2026-08-12T12:55:57+00:00", "2026-08-12T18:04:57+00:00", 47.4, 89.6, 7.9, 2.1),
        (2, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 89.7, 93.4, 0.7, 0.2),
    ):
        con.execute(
            "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
            "energy_added_kwh, ac_energy_kwh, cost, duration_min, charge_type, location_type, "
            "max_power_kw) VALUES (?,1,?,?,?,?,?,?,?, 10.0, 'AC', 'HOME', 1.6)",
            (cid, s, e, ss, es, kwh, kwh, cost))
    con.commit(); con.close()


def test_the_energy_and_the_money_are_the_same_before_and_after(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    before = db_reader.get_charge_stats()

    db_reader.merge_charges(1, 2)

    after = db_reader.get_charge_stats()
    assert after["total_kwh"] == pytest.approx(before["total_kwh"])
    assert after["total_cost"] == pytest.approx(before["total_cost"])
    assert after["priced_kwh"] == pytest.approx(before["priced_kwh"])
    assert after["peak_power_kw"] == before["peak_power_kw"]


def test_the_euros_actually_spent_are_the_same(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    before = db_reader._priced_euros(db_reader._get(), 1)

    db_reader.merge_charges(1, 2)

    assert db_reader._priced_euros(db_reader._get(), 1) == pytest.approx(before)


def test_the_average_price_per_kwh_is_the_same(tmp_path, monkeypatch):
    """Il €/kWh medio divide euro per kWh: entrambi sommano al gruppo, quindi non si muove.
    (Il numero di ricariche prezzate invece cala, ed è misurato altrove.)"""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    before = db_reader.get_charge_stats()

    db_reader.merge_charges(1, 2)

    after = db_reader.get_charge_stats()
    assert after["avg_price"] == pytest.approx(before["avg_price"])


def test_the_ac_dc_energy_is_the_same_even_though_the_count_drops(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    before = db_reader.get_ac_dc_stats()

    db_reader.merge_charges(1, 2)

    after = db_reader.get_ac_dc_stats()
    assert after["ac"]["kwh"] == pytest.approx(before["ac"]["kwh"])
    assert after["dc"]["kwh"] == pytest.approx(before["dc"]["kwh"])
    assert after["total"] == before["total"] - 1        # cambia solo il CONTEGGIO


def test_the_wrong_way_of_doing_it_would_lose_energy(tmp_path, monkeypatch):
    """Il modo sbagliato — escludere i figli anche dalle somme — e quanto costerebbe.

    Non è una critica teorica: è il primo modo in cui viene da scriverlo, e su questa coppia
    farebbe sparire 0,7 kWh su 8,6, cioè l'8%. Questo test tiene la differenza visibile."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    db_reader.merge_charges(1, 2)
    db = db_reader._get()

    giusto = db.execute("SELECT SUM(energy_added_kwh) FROM charges").fetchone()[0]
    sbagliato = db.execute(
        "SELECT SUM(energy_added_kwh) FROM charges WHERE merged_into_id IS NULL").fetchone()[0]

    assert giusto == pytest.approx(8.6)
    assert sbagliato == pytest.approx(7.9)
    assert giusto - sbagliato == pytest.approx(0.7)     # l'energia che sparirebbe
