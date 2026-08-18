"""I consumi in cache devono appartenere all'auto selezionata (#253, rastrellata).

`_period_cache` è UNA sola, condivisa da quattro endpoint — ripartizione energia, consumo getPlugIn,
periodo a scelta, dall'ultima ricarica — e ha nove forme di chiave. **Nessuna nomina l'auto**:

    "plugin6w"                    f"p:day:{data}"        f"p:week:{data}"
    f"p:month:{mese}"             f"p:all:{primo}:{data}"  f"r:{inizio}:{fine}"
    f"p:sincecharge:{ts}"         f"p:reportmonth:{mese}:{ts}"

Guardi le Statistiche della C10, i numeri finiscono in `p:day:2026-08-18`, passi alla T03 entro
trenta minuti e ti servono quelli della C10.

🔑 **E questo l'ha aperto la v3.14.3.** Prima, ogni lettura tornava comunque i dati della PRIMA auto
dell'account: la cache condivisa era sbagliata ma uniforme. Da quando la lettura segue il selettore,
la chiave condivisa mescola. Corretta la sorgente, nessuno si è chiesto dove finivano i risultati —
è la stessa forma del memo dell'immagine corretto nella v3.14.1, in un altro modulo.
→ [[feedback-gate-a-feature-find-every-copy]]
"""
import pytest

pytest.importorskip("fastapi", reason="serve fastapi")

A, B = "LFZAAA0000000001", "LFZBBB0000000002"


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Due auto, e un cloud che risponde con l'energia DELL'AUTO SELEZIONATA."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    for vid, vin, ct in ((1, A, "T03"), (2, B, "C10")):
        c.execute("INSERT INTO vehicles (id,vin,car_type) VALUES (?,?,?)", (vid, vin, ct))
        c.execute("INSERT INTO trips (vehicle_id,started_at,ended_at,distance_km,duration_min,"
                  "start_soc,end_soc) VALUES (?,'2026-08-16T08:00:00+00:00',"
                  "'2026-08-16T08:30:00+00:00',100,30,60,50)", (vid,))
        c.execute("INSERT INTO charges (vehicle_id,started_at,ended_at,energy_added_kwh,"
                  "duration_min,start_soc,end_soc) VALUES (?,'2026-08-15T20:00:00+00:00',"
                  "'2026-08-15T22:00:00+00:00',20,120,40,80)", (vid,))
    c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('setup_complete','1')")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    main._period_cache.clear()

    # 11,1 kWh per la T03 · 77,7 per la C10 — disgiunti, come nella rastrellata
    def _per_car(*a, **k):
        vin = (db_reader.get_setting(db_reader.ACTIVE_VEHICLE_SETTING, "") or "").lower()
        kwh = 77.7 if vin == B.lower() else 11.1
        return {"total_kwh": kwh, "driving_kwh": kwh, "ac_kwh": 0.0, "other_kwh": 0.0,
                "driving_pct": 100.0, "ac_pct": 0.0, "other_pct": 0.0}

    # getPlugIn ha una forma sua: il finto deve essere quello VERO, o il template rende vuoto e
    # il test diventa rosso per colpa mia invece che del difetto.
    def _per_car_plugin(*a, **k):
        vin = (db_reader.get_setting(db_reader.ACTIVE_VEHICLE_SETTING, "") or "").lower()
        kwh = 77.7 if vin == B.lower() else 11.1
        return {"total_energy_kwh": kwh, "total_mileage_km": 100.0,
                "elec_kwh_100km": kwh, "fuel_l_100km": None,
                "ec_driving_kwh": None, "ec_ac_kwh": None, "ec_other_kwh": None,
                "driving_kwh": None, "parked_kwh": None}

    for name in ("get_energy_breakdown_range", "get_energy_breakdown",
                 "get_consumption_rank", "get_cumulative_summary"):
        monkeypatch.setattr(main.command_client, name, _per_car, raising=False)
    monkeypatch.setattr(main.command_client, "get_plugin_consumption", _per_car_plugin,
                        raising=False)
    return db_reader, main


def _body(main, db_reader, vin, call):
    import asyncio
    db_reader.set_active_vehicle(vin)
    return asyncio.run(call()).body.decode()


@pytest.mark.parametrize("endpoint,kwargs", [
    ("energy_period", {"period": "day"}),
    ("energy_period", {"period": "week"}),
    ("energy_period", {"period": "month"}),
    ("energy_period", {"period": "alltime"}),
    ("energy_period", {"start": "2026-08-01", "end": "2026-08-17"}),
    ("energy_since_charge", {}),
    ("plugin_consumption", {}),
])
def test_the_second_car_is_not_served_the_first_ones_numbers(rig, endpoint, kwargs):
    """Una per forma di chiave: basta che UNA non porti l'auto perché quella schermata menta."""
    db_reader, main = rig
    fn = getattr(main, endpoint)

    a = _body(main, db_reader, A, lambda: fn(_Req(), **kwargs))
    b = _body(main, db_reader, B, lambda: fn(_Req(), **kwargs))

    assert "11,1" in a or "11.1" in a, f"la T03 non mostra i suoi 11,1:\n{a[:400]}"
    assert "11,1" not in b and "11.1" not in b, (
        f"la C10 sta mostrando i numeri della T03 ({endpoint} {kwargs}):\n{b[:400]}")


def test_going_back_finds_its_own_again(rig):
    """La cache deve tenere le due auto separate in ENTRAMBE le direzioni, non solo la prima volta."""
    db_reader, main = rig
    _body(main, db_reader, A, lambda: main.energy_period(_Req(), period="day"))
    _body(main, db_reader, B, lambda: main.energy_period(_Req(), period="day"))
    again = _body(main, db_reader, A, lambda: main.energy_period(_Req(), period="day"))
    assert "11,1" in again or "11.1" in again, again[:400]


def test_no_key_reaches_the_cache_without_a_car():
    """Il guardiano, non il test sopra: nove forme di chiave oggi, e la decima la scriverà qualcuno
    copiando la riga accanto. Nessuno tocca `_period_cache` se non attraverso l'aiutante che ci
    mette l'auto davanti."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()
    bad = [f"{n}: {ln.strip()}" for n, ln in enumerate(src.split("\n"), 1)
           if re.search(r"_period_cache\s*[\[.]", ln) and "_pcache_key" not in ln
           and not re.search(r"_period_cache\.(clear|pop)\b", ln)
           and "def _pcache_key" not in ln]
    assert not bad, "queste toccano la cache senza l'auto:\n  " + "\n  ".join(bad)
