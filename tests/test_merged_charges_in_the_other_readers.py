"""Gli altri lettori delle ricariche davanti a una coppia unita.

Non tutti cambiano risposta, e i due gruppi vanno separati con una misura, non a intuito:

- chi tratta una ricarica come UN EVENTO (un segnaposto sulla mappa del viaggio, una voce nel
  calendario della wallbox, i km dall'ultima ricarica, «quando comincia la prossima») deve vedere
  la sessione intera, o mostra due volte una cosa sola;
- chi fa una media PESATA SULL'ENERGIA (il €/kWh miscelato) non cambia per costruzione, perché i
  pezzi sommano al gruppo — ed è quello che questi test verificano invece di darlo per buono.
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


def _charge(path, cid, start, end, ssoc, esoc, kwh, cost=None, odo=None, lat=None, lon=None):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
        "energy_added_kwh, ac_energy_kwh, cost, duration_min, charge_type, location_type, "
        "max_power_kw, odometer_km, latitude, longitude) "
        "VALUES (?,?,?,?,?,?,?,?,?,?, 'AC', 'HOME', 1.6, ?,?,?)",
        (cid, 1, start, end, ssoc, esoc, kwh, kwh, cost, 10.0, odo, lat, lon))
    con.commit(); con.close()


def _split_pair(path, odo=(1000.0, 1000.0)):
    _charge(path, 1, "2026-08-12T12:55:57+00:00", "2026-08-12T18:04:57+00:00", 47.4, 89.6, 7.9,
            2.1, odo[0], 45.5, 9.2)
    _charge(path, 2, "2026-08-12T18:05:27+00:00", "2026-08-12T18:38:39+00:00", 89.7, 93.4, 0.7,
            0.2, odo[1], 45.5, 9.2)


def _charging_positions(path):
    """La wallbox guarda solo le ricariche che hanno telemetria di carica sotto."""
    con = sqlite3.connect(path)
    for ts in ("2026-08-12T13:00:00+00:00", "2026-08-12T18:20:00+00:00"):
        con.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging) "
                    "VALUES (1, ?, 60.0, 1)", (ts,))
    con.commit(); con.close()


# ── chi deve vedere UNA sessione ────────────────────────────────────────────────
def test_the_wallbox_calendar_lists_one_session_spanning_the_whole_charge(tmp_path, monkeypatch):
    """Due voci nel calendario della wallbox sono due interrogazioni a Home Assistant e due
    attribuzioni per una ricarica sola. E la finestra deve arrivare fino alla fine vera: fermarsi
    alla fine del primo pezzo lascerebbe fuori i kWh del secondo."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p); _charging_positions(p)
    assert len(db_reader._wallbox_home_charges_raw()) == 2

    db_reader.merge_charges(1, 2)

    rows = db_reader._wallbox_home_charges_raw()
    assert len(rows) == 1
    assert rows[0]["energy_added_kwh"] == pytest.approx(8.6)


def test_a_trip_shows_one_charge_marker_not_two(tmp_path, monkeypatch):
    """⚠️ Fuori casa, di proposito: questa funzione salta le ricariche di casa (la mappa del
    viaggio non le segna), quindi con un fixture HOME il test sarebbe stato verde di niente."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    con = sqlite3.connect(p)
    con.execute("UPDATE charges SET location_type='FAST'")
    con.commit(); con.close()
    assert len(db_reader._trip_stop_charges(db_reader._get(), 1, "2026-08-12T00:00:00+00:00")) == 2

    db_reader.merge_charges(1, 2)

    assert len(db_reader._trip_stop_charges(db_reader._get(), 1, "2026-08-12T00:00:00+00:00")) == 1


def test_the_kilometres_since_the_previous_charge_do_not_count_a_zero_hop(tmp_path, monkeypatch):
    """Fra i due pezzi l'auto è ferma: l'odometro è lo stesso. Contati come due ricariche, il
    secondo dice «0 km dalla precedente» — un dato inventato dallo split, non guidato da nessuno."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    _charge(p, 3, "2026-08-13T09:00:00+00:00", "2026-08-13T10:00:00+00:00", 40.0, 80.0, 20.0,
            5.0, 1120.0)
    assert db_reader._km_since_previous_map().get(3) == pytest.approx(120.0)

    db_reader.merge_charges(1, 2)

    m = db_reader._km_since_previous_map()
    assert m.get(3) == pytest.approx(120.0)      # invariato: la distanza vera non cambia
    assert 2 not in m                            # il pezzo non è più una tappa


def test_the_next_charge_after_a_trip_is_the_session_not_its_second_piece(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    db_reader.merge_charges(1, 2)

    nxt = db_reader._next_charge_start_utc(db_reader._get(), "2026-08-12T10:00:00+00:00")

    assert nxt is not None and nxt.startswith("2026-08-12T12:55")


# ── chi NON cambia, dimostrato ──────────────────────────────────────────────────
def test_the_blended_price_per_kwh_does_not_move(tmp_path, monkeypatch):
    """Il €/kWh miscelato è una media pesata sull'energia, e i pezzi sommano al gruppo: unire non
    lo sposta. Misurato, non assunto — se un giorno si sposterà, questo test lo dirà."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    before = db_reader.blended_price_at(1, "2026-08-13T00:00:00+00:00")

    db_reader.merge_charges(1, 2)

    after = db_reader.blended_price_at(1, "2026-08-13T00:00:00+00:00")
    assert after == pytest.approx(before, rel=0.02)


def test_the_learned_wallbox_position_does_not_move(tmp_path, monkeypatch):
    """I due pezzi hanno le stesse coordinate: toglierne uno non sposta il punto imparato."""
    p = _setup(tmp_path, monkeypatch); _split_pair(p)
    before = db_reader._learned_wallbox_location(1)

    db_reader.merge_charges(1, 2)

    assert db_reader._learned_wallbox_location(1) == before
