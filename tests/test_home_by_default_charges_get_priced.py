"""«Ricarico sempre a casa»: la ricarica nasce col distintivo verde — e senza prezzo.

L'opzione della v3.14.15 (disc #255, @CartusGress) fa nascere la ricarica già `location_type='HOME'`
invece che senza tipo, per risparmiare a chi ricarica solo a casa un clic di conferma a ogni sessione.
Ma il motore dei costi gira **solo su una conferma** — a mano, o quella automatica della wallbox.
Nascendo già confermata, la ricarica non passa da nessuna delle due: distintivo «Casa», costo «—».

Il danno non è il trattino: è che la spesa del periodo e la media €/kWh contano solo le ricariche
prezzate, quindi **le escludono tutte**. Chi accende quell'opzione smette di vedere quanto spende,
e non c'è niente sullo schermo che glielo dica. → [[a-feature-switch-must-gate-the-data]]

🔑 La riparazione è la stessa cosa della correzione, e per questo non serve inventare una regola sul
«quale prezzo»: si passa da `update_charge_type`, la STESSA strada di una conferma a mano. Una
ricarica vecchia viene prezzata esattamente come se l'utente ne premesse il distintivo oggi — e le
fasce orarie leggono comunque l'ora della ricarica, non quella di adesso.

⚠️ Un costo già scritto non si tocca: si riempie solo ciò che è `NULL`. Un costo confermato è
congelato («solo le ricariche nuove»), e una ricarica segnata gratis ha `cost = 0.0`, che non è NULL.
"""
import sqlite3

import pytest

import db_reader


@pytest.fixture
def archivio(tmp_path, monkeypatch):
    import db as poller_db
    path = str(tmp_path / "c.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V1')")
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.update_charge_price("price_home_kwh", 0.25)
    return path


def _ricarica(path, cid, tipo="HOME", costo=None, kwh=10.0):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
        "energy_added_kwh, duration_min, charge_type, location_type, cost) "
        "VALUES (?,1,'2026-08-12T20:00:00+00:00','2026-08-12T22:00:00+00:00',40,60,?,120,'AC',?,?)",
        (cid, kwh, tipo, costo))
    con.commit(); con.close()


def _costo(path, cid):
    con = sqlite3.connect(path)
    v = con.execute("SELECT cost FROM charges WHERE id = ?", (cid,)).fetchone()[0]
    con.close()
    return v


def test_a_charge_born_home_gets_its_price(archivio):
    """Il difetto in una riga: 10 kWh a 0,25 fanno 2,50 €, non «—»."""
    db_reader.set_setting("home_charges_default", "1")
    _ricarica(archivio, 1)
    assert db_reader.price_default_home_charges() == 1
    assert _costo(archivio, 1) == pytest.approx(2.50)


def test_the_backlog_is_repaired_too_not_only_new_ones(archivio):
    """La correzione deve raggiungere chi HA GIÀ il difetto, non solo chi arriva dopo: chi ha acceso
    l'opzione settimane fa ha un mucchio di ricariche col distintivo e senza prezzo.
    → [[migration-on-the-alter-misses-who-already-updated]]"""
    db_reader.set_setting("home_charges_default", "1")
    for cid in (1, 2, 3):
        _ricarica(archivio, cid)
    assert db_reader.price_default_home_charges() == 3
    assert all(_costo(archivio, c) == pytest.approx(2.50) for c in (1, 2, 3))


def test_it_never_touches_a_cost_that_is_already_there(archivio):
    """Un costo confermato è congelato, e uno segnato gratis vale zero: né l'uno né l'altro si
    riscrive. Questa è la guardia che rende la passata sicura da rifare a ogni pagina."""
    db_reader.set_setting("home_charges_default", "1")
    _ricarica(archivio, 1, costo=9.99)      # prezzata a mano
    _ricarica(archivio, 2, costo=0.0)       # segnata gratis (#120)
    assert db_reader.price_default_home_charges() == 0
    assert _costo(archivio, 1) == 9.99
    assert _costo(archivio, 2) == 0.0


def test_it_does_nothing_when_the_option_is_off(archivio):
    """Senza l'opzione accesa non c'è nessun difetto da riparare, e una ricarica senza tipo che
    l'utente non ha ancora confermato deve restare sua."""
    db_reader.set_setting("home_charges_default", "0")
    _ricarica(archivio, 1)
    assert db_reader.price_default_home_charges() == 0
    assert _costo(archivio, 1) is None


def test_it_does_nothing_without_a_home_tariff(archivio):
    """Senza una tariffa di casa non c'è niente con cui prezzare: la passata deve tirarsi indietro
    invece di riselezionare le stesse righe a ogni rendering di pagina."""
    db_reader.set_setting("home_charges_default", "1")
    db_reader.update_charge_price("price_home_kwh", 0.0)
    _ricarica(archivio, 1)
    assert db_reader.price_default_home_charges() == 0
    assert _costo(archivio, 1) is None


def test_running_it_twice_finds_nothing_the_second_time(archivio):
    """Gira a ogni pagina: la seconda passata non deve avere niente da fare."""
    db_reader.set_setting("home_charges_default", "1")
    _ricarica(archivio, 1)
    assert db_reader.price_default_home_charges() == 1
    assert db_reader.price_default_home_charges() == 0


def test_the_sweep_runs_on_page_renders(archivio):
    """Il rubinetto: la passata dev'essere agganciata dove girano le altre, o ripara solo quando
    qualcuno si ricorda di chiamarla."""
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("web/main.py").read_text()
    ctx = re.search(r"def _ctx\(\*\*kwargs\):.*?\n    lang = ", src, re.S)
    assert ctx, "_ctx ha cambiato forma: aggiorna questa ricerca"
    assert "price_default_home_charges()" in ctx.group(0), (
        "la passata non è agganciata al rendering delle pagine, accanto alle sue sorelle")
