"""Uno zero che nessuno ha scritto non è un prezzo — e le due modalità devono dire la stessa cosa.

La pagina Costi precompila **tutti e quattro** i campi con `0.00`. Chi riempie solo «Casa» e salva
si porta in archivio anche `price_ac_kwh=0.00`, `price_fast_kwh=0.00`, `price_hpc_kwh=0.00` senza
averli mai toccati: `save_prices` accetta qualunque valore non vuoto, e la stringa «0.00» non è
vuota.

Poi le due modalità leggono quello stesso zero in modo **opposto** — misurato:

    40 kWh in rapida, tariffa fissa   ->  None   (non prezzata, «—»)
    40 kWh in rapida, fasce orarie    ->  0.0    (PREZZATA, gratis)

Con le fasce, una ricarica rapida pagata davvero risulta gratis, entra nella media €/kWh come
prezzata e la tira giù, ed entra nella miscela a tasso zero diluendo il costo di ogni viaggio dopo.

🔑 **La regola scelta è quella che il ramo a tariffa fissa ha sempre avuto**: una tariffa BASE a zero
non è un prezzo. Non è una semantica nuova, è quella esistente propagata all'altro ramo — e va in
questa direzione, non nell'altra, perché il contrario prezzerebbe a zero le ricariche di chiunque
abbia quello 0.00 in archivio per colpa del modulo.

⚠️ Uno zero scritto **dentro una fascia** resta un prezzo: lì l'utente l'ha digitato apposta, e
`_resolve_band_price` lo distingue (torna `is_set=True` solo quando la fascia prezza quel tipo).
E per «questa ricarica è stata gratis» esiste il contrassegno apposta (#120), che è il posto giusto.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import db_reader


T0 = datetime(2026, 8, 12, 20, 0, 0, tzinfo=timezone.utc)
NOTTE = [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59",
          "prices": {"HOME": 0.18}}]          # una fascia che prezza SOLO casa


@pytest.fixture
def archivio(monkeypatch):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE positions (vehicle_id INT, recorded_at TEXT, charging INT, "
                "charge_voltage_v REAL, charge_current_a REAL)")
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "_conn_rw", lambda: con)
    # l'utente riempie SOLO «Casa»; la pagina manda 0.00 negli altri tre
    for k, v in (("price_home_kwh", 0.25), ("price_ac_kwh", 0.0),
                 ("price_fast_kwh", 0.0), ("price_hpc_kwh", 0.0)):
        db_reader.update_charge_price(k, v)
    return con


def _ricarica(tipo="FAST", kwh=40.0):
    return {"location_type": tipo, "energy_added_kwh": kwh, "ac_energy_kwh": None,
            "started_at": T0.isoformat(), "ended_at": (T0 + timedelta(hours=1)).isoformat()}


def _cfg(modo, bands=None, method="start"):
    return {"mode": modo, "modes": {t: modo for t in db_reader._TOU_TYPES},
            "method": method, "bands": bands or []}


@pytest.mark.parametrize("method", ["start", "split"])
def test_a_tariff_nobody_typed_prices_nothing_in_either_mode(archivio, method):
    """Il difetto in una riga: lo stesso zero in archivio, la stessa ricarica, due risposte."""
    fissa = db_reader.compute_cost(_ricarica(), _cfg("flat"))
    fasce = db_reader.compute_cost(_ricarica(), _cfg("tou", NOTTE, method))
    assert fissa is None, f"il ramo a tariffa fissa ha prezzato {fissa}"
    assert fasce is None, (
        f"con le fasce ({method}) una rapida da 40 kWh risulta prezzata {fasce} € — gratis: "
        f"entra nella media come pagata a zero e diluisce la miscela")


def test_a_zero_typed_INSIDE_a_band_is_still_a_price(archivio):
    """La contropartita: se l'utente scrive zero **in una fascia**, quello è un prezzo e resta.
    Senza questa distinzione la correzione butterebbe via anche gli zeri voluti."""
    gratis = [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59",
               "prices": {"FAST": 0.0}}]
    c = db_reader.compute_cost(_ricarica(), _cfg("tou", gratis))
    assert c == 0.0, f"lo zero digitato dentro una fascia è andato perso: {c!r}"


def test_a_real_tariff_still_prices_normally(archivio):
    """La guardia contro il rimedio peggiore del male: le tariffe vere continuano a prezzare."""
    assert db_reader.compute_cost(_ricarica("HOME", 10.0), _cfg("flat")) == pytest.approx(2.50)
    assert db_reader.compute_cost(_ricarica("HOME", 10.0), _cfg("tou", NOTTE)) == pytest.approx(1.80)


def test_the_costs_page_no_longer_pre_fills_every_field_with_zero(archivio):
    """E il rubinetto, non solo il secchio: il campo vuoto non finisce in archivio, quindi da qui in
    avanti «non impostato» resta distinguibile da «zero»."""
    import pathlib
    import re
    html = pathlib.Path(__file__).resolve().parents[1].joinpath("web/templates/costs.html").read_text()
    m = re.search(r"value=\"\{\{ settings\.get\(setting_key,\s*'([^']*)'\)\s*\}\}\"", html)
    assert m, "il campo del prezzo ha cambiato forma: aggiorna questa ricerca"
    assert m.group(1) == "", (
        f"il campo si precompila ancora con {m.group(1)!r}: chi salva senza toccarlo si porta in "
        f"archivio uno zero che non ha mai scritto")
