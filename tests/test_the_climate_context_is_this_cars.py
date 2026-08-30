"""Il comando clima da MQTT deve leggere il pannello DELL'AUTO a cui è diretto.

Ventola e ricircolo sono due controlli scrivibili in Home Assistant. Il cloud non ha un comando
«cambia solo la ventola»: bisogna rimandare il pannello intero — modo, temperatura, ricircolo,
velocità — quindi Mate rilegge lo stato attuale e ne cambia un pezzo solo.

Lo rileggeva però dall'**ultima riga di `positions` in assoluto**, senza filtro sull'auto, mentre il
poller interroga a giro tutte le auto dell'account e ognuna scrive le sue. Su due auto, il comando
mandato alla C10 poteva portarle il modo e la temperatura della T03: si chiede più ventola e la
macchina cambia anche il resto.

Il gestore MQTT il VIN ce l'ha già in mano — lo usa nella riga dopo per mandare il comando.
"""
import importlib.util
import pathlib
import sys

import pytest


def _poller_main():
    """poller/main.py sotto un nome suo — un `import main` nudo prende quello del web.
    → [[mate-two-main-py-collision]]"""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_clima", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["poller_main_clima"] = mod
    spec.loader.exec_module(mod)
    return mod


PM = _poller_main()


A_VIN, B_VIN = "LFZAAA0000000001", "LFZBBB0000000002"


@pytest.fixture
def due_auto(tmp_path):
    """Due auto, due pannelli diversi, e la riga PIÙ RECENTE è quella dell'auto B."""
    import db as poller_db
    path = str(tmp_path / "p.db")
    d = poller_db.Database(path)
    c = d._conn
    c.execute("INSERT INTO vehicles (id, vin) VALUES (1, ?)", (A_VIN,))
    c.execute("INSERT INTO vehicles (id, vin) VALUES (2, ?)", (B_VIN,))
    # A: caldo a 22° con ricircolo · B (più recente): freddo a 30° con aria esterna
    c.execute("INSERT INTO positions (vehicle_id, recorded_at, climate_mode, recirculation, "
              "fan_level, climate_target_temp) VALUES (1,'2026-08-12T10:00:00+00:00',3,1,2,22)")
    c.execute("INSERT INTO positions (vehicle_id, recorded_at, climate_mode, recirculation, "
              "fan_level, climate_target_temp) VALUES (2,'2026-08-12T10:00:05+00:00',1,0,6,30)")
    c.commit()
    return d


def test_it_reads_the_panel_of_the_car_the_command_is_for(due_auto):
    """Il difetto in una riga: chiedendo il pannello dell'auto A si deve ottenere quello di A,
    anche se l'ultima riga scritta è di B."""
    modo, ricircolo, ventola, temp = PM._climate_ctx_from_db(due_auto, A_VIN)
    assert (modo, ricircolo, ventola, temp) == ("hot", "in", 2, 22), (
        f"ha letto ({modo}, {ricircolo}, {ventola}, {temp}): è il pannello dell'altra auto")


def test_the_other_car_gets_its_own(due_auto):
    assert PM._climate_ctx_from_db(due_auto, B_VIN) == ("cold", "out", 6, 30)


def test_an_unknown_vin_falls_back_to_the_safe_defaults(due_auto):
    """Un VIN che non conosciamo non deve prendersi il pannello di un'auto a caso: meglio i valori
    di riposo, che è quello che il codice fa già quando non trova niente."""
    assert PM._climate_ctx_from_db(due_auto, "LFZZZZ0000000009") == ("wind", "out", 3, 26)


def test_both_handlers_pass_the_vin(due_auto):
    """Il rubinetto: i due gestori che compongono il pannello devono passare il VIN che hanno già in
    mano. Correggere la funzione e lasciare i chiamanti com'erano non sistemerebbe niente."""
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("poller/main.py").read_text()
    chiamate = re.findall(r"_climate_ctx_from_db\(([^)]*)\)", src)
    senza_vin = [c for c in chiamate if "vin" not in c and "def " not in c]
    assert not senza_vin, f"queste chiamate non nominano l'auto: {senza_vin}"
