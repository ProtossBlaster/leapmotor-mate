"""Una ricarica unita vale UNO per chi conta, e la sessione intera per chi divide.

Il difetto che questi test tengono è quello che oggi è uscito pubblico sui viaggi (#247): la
fusione esisteva, ma un lettore non la conosceva e mostrava mezza verità. Sulle ricariche i
lettori sono sedici, quindi la regola va fissata dove i numeri si vedono — i contatori, le medie,
e soprattutto il SoH, che di una ricarica spezzata prende energia parziale e ΔSoC parziale.

⚠️ Le SOMME non compaiono qui perché non cambiano per costruzione (i pezzi sommano al gruppo);
sono misurate a parte in test_merged_charges_do_not_move_the_totals.py.
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


def _charge(path, cid, start, end, ssoc, esoc, kwh, cost=None, ctype="AC", loc="HOME"):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
        "energy_added_kwh, cost, duration_min, charge_type, location_type, max_power_kw) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, 1, start, end, ssoc, esoc, kwh, cost, 10.0, ctype, loc, 1.6))
    con.commit(); con.close()


def _split_pair(path):
    """Un attacco solo, spezzato dall'auto: 30 secondi di buco fra i due pezzi."""
    _charge(path, 1, "2026-08-12T18:00:00+00:00", "2026-08-12T18:04:57+00:00", 47.4, 89.6, 7.9, 2.1)
    _charge(path, 2, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 89.7, 93.4, 0.7, 0.2)


# ── i contatori ─────────────────────────────────────────────────────────────────
def test_the_session_count_drops_by_one_when_two_pieces_become_one(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    assert db_reader.get_charge_stats()["session_count"] == 2

    db_reader.merge_charges(1, 2)

    assert db_reader.get_charge_stats()["session_count"] == 1


def test_the_priced_count_counts_charges_not_pieces(tmp_path, monkeypatch):
    """Il €/kWh medio divide per questo: due pezzi prezzati sono UNA ricarica prezzata."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    assert db_reader.get_charge_stats()["priced_count"] == 2

    db_reader.merge_charges(1, 2)

    assert db_reader.get_charge_stats()["priced_count"] == 1


def test_the_average_soc_delta_is_the_whole_session(tmp_path, monkeypatch):
    """Spezzata dà due saltini (42,2 e 3,7 punti); intera dà il salto vero: 46 punti."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    assert db_reader.get_charge_stats()["avg_soc_delta"] == pytest.approx(22.9, abs=0.05)

    db_reader.merge_charges(1, 2)

    assert db_reader.get_charge_stats()["avg_soc_delta"] == pytest.approx(46.0, abs=0.1)


def test_the_average_duration_is_the_window_the_user_saw(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _split_pair(p)

    db_reader.merge_charges(1, 2)

    # 18:00 → 18:38:39 = 38,65 min = 0,64 h (scelta dichiarata: la pausa dentro conta)
    assert db_reader.get_charge_stats()["avg_duration_h"] == pytest.approx(0.6, abs=0.05)


def test_the_banner_to_confirm_counts_charges_not_pieces(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    _charge(p, 1, "2026-08-12T18:00:00+00:00", "2026-08-12T18:04:57+00:00", 47.4, 89.6, 7.9, loc=None)
    _charge(p, 2, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 89.7, 93.4, 0.7, loc=None)
    assert db_reader.unconfirmed_charges_count() == 2

    db_reader.merge_charges(1, 2)

    assert db_reader.unconfirmed_charges_count() == 1


def test_ac_dc_counts_one_session_but_keeps_all_the_energy(tmp_path, monkeypatch):
    """Il cancello che conta davvero: contare i gruppi senza PERDERE i kWh dei pezzi. Escludere
    i figli e basta farebbe sparire la loro energia dal grafico AC/DC."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    before = db_reader.get_ac_dc_stats()
    assert before["ac"]["count"] == 2 and before["ac"]["kwh"] == pytest.approx(8.6)

    db_reader.merge_charges(1, 2)

    after = db_reader.get_ac_dc_stats()
    assert after["ac"]["count"] == 1
    assert after["ac"]["kwh"] == pytest.approx(8.6)      # l'energia NON si perde
    assert after["total"] == 1


# ── il SoH ──────────────────────────────────────────────────────────────────────
def _long_split_pair(path):
    """Lo stesso split coi tempi VERI della beta #29: la prima parte dura 309 minuti, il buco 30
    secondi, la seconda 33. Il SoH ha bisogno di una finestra in cui il SoC salga davvero — su
    cinque minuti scarta il punto, e un test che non produce punti non dimostra niente."""
    _charge(path, 1, "2026-08-12T12:55:57+00:00", "2026-08-12T18:04:57+00:00", 47.4, 89.6, 7.9, 2.1)
    _charge(path, 2, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 89.7, 93.4, 0.7, 0.2)


def _telemetry(path):
    """I campioni con cui il SoH integra l'energia: senza questi non esiste nessun punto.
    Coprono l'intera sessione, pausa compresa, come farebbe il poller.

    ⚠️ La corrente non è a caso: 400 V × 11,6 A per 5h43 danno ~26,6 kWh su 46 punti di
    SoC, cioè un pacco da ~65 kWh — quello dell'auto. Con una corrente più alta la stima
    usciva a 99 kWh e il SoH scartava il punto come implausibile, lasciando il test muto."""
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat("2026-08-12T12:55:57+00:00")
    n = 343                                     # minuti da 12:55:57 a 18:38:39
    con = sqlite3.connect(path)
    for i in range(0, n + 1, 5):
        ts = (t0 + timedelta(minutes=i)).isoformat()
        soc = 47.4 + (93.4 - 47.4) * i / n
        con.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, "
                    "charge_voltage_v, charge_current_a) VALUES (1, ?, ?, 1, 400.0, 11.6)",
                    (ts, round(soc, 1)))
    con.commit(); con.close()


def test_the_state_of_health_sees_one_session_not_two(tmp_path, monkeypatch):
    """Il punto dell'intero lavoro. Il SoH divide l'energia misurata per il ΔSoC: su una ricarica
    spezzata prende un pezzo di energia e un pezzo di delta, e su ognuno dei due la stima è di una
    capacità che l'auto non ha. Unita, la sessione torna quella vera — 47,4 → 93,4."""
    p = _setup(tmp_path, monkeypatch); _long_split_pair(p); _telemetry(p)
    before = db_reader.get_battery_health()["points"]
    assert len(before) == 1                                        # solo il pezzo con ΔSoC ≥ 12
    assert before[0]["soc_delta"] == pytest.approx(42.2, abs=0.1)  # metà sessione

    db_reader.merge_charges(1, 2)

    pts = db_reader.get_battery_health()["points"]
    assert len(pts) == 1
    assert pts[0]["soc_delta"] == pytest.approx(46.0, abs=0.1)     # la sessione intera
