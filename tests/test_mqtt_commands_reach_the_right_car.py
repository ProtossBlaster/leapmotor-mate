"""Un comando MQTT arriva alla macchina del suo topic, con i valori di QUELLA macchina.

I topic sono `{prefisso}/{vin}/command`, e il ponte si sottoscrive col jolly: il VIN lo porta il
messaggio. Da lì in poi tutto deve seguire quel VIN — il PIN con cui si autorizza, e la scala
nativa dei finestrini, che è del MODELLO (B10/C10/B05 = 0-10, T03 = 0-100).

🔴 `_mqtt_windows_native` leggeva il modello da `client._vehicle`, cioè dalla PRIMA auto
dell'account: con una B10 e una T03 sullo stesso Mate, «apri i finestrini» mandato alla T03 usava
la scala della B10 e le apriva a un decimo. Forma 1 della lista dell'audit — un valore preso dalla
prima auto dentro un'operazione che riguarda un'altra.
"""
import importlib.util
import pathlib
import sqlite3
import types

import db as poller_db


def _poller_main():
    """poller/main.py sotto il suo nome: si scontra con web/main.py, come sanno gli altri test
    del ponte MQTT. → [[mate-two-main-py-collision]]"""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


poller_main = _poller_main()


def _client(vehicles):
    """Un client finto con la lista delle auto, come quello vero dopo il login."""
    vs = [types.SimpleNamespace(vin=v, car_type=c) for v, c in vehicles]
    return types.SimpleNamespace(_vehicle=vs[0], _vehicles=vs, _api=None)


def _db(tmp_path, pins):
    d = poller_db.Database(str(tmp_path / "m.db"))
    con = sqlite3.connect(str(tmp_path / "m.db"))
    for i, (vin, _ct) in enumerate(pins, start=1):
        con.execute("INSERT INTO vehicles (id, vin) VALUES (?, ?)", (i, vin))
    con.commit(); con.close()
    return d


CARS = [("VIN_B10", "B10"), ("VIN_T03", "T03")]


def test_the_window_scale_follows_the_car_the_command_is_for(tmp_path):
    """20% su una B10 sono 2 sulla scala nativa; sulla T03 sono 20. Il numero giusto dipende
    dall'auto del comando, non da quale sia la prima dell'account."""
    c = _client(CARS)

    assert poller_main._mqtt_windows_native(c, 20, vin="VIN_B10") == "2"
    assert poller_main._mqtt_windows_native(c, 20, vin="VIN_T03") == "20"


def test_closing_is_zero_on_both_scales(tmp_path):
    c = _client(CARS)

    assert poller_main._mqtt_windows_native(c, 0, vin="VIN_B10") == "0"
    assert poller_main._mqtt_windows_native(c, 0, vin="VIN_T03") == "0"


def test_an_unknown_vin_falls_back_to_the_first_car(tmp_path):
    """Non si inventa una scala: senza VIN riconosciuto resta il comportamento di prima."""
    c = _client(CARS)

    assert poller_main._mqtt_windows_native(c, 20, vin="") == "2"        # la prima è la B10


def test_each_command_carries_its_own_vin_and_pin(tmp_path):
    """Il giro completo: due messaggi MQTT, uno per auto, e ognuno arriva al cloud con il VIN del
    suo topic e il PIN di quella macchina."""
    d = _db(tmp_path, CARS)
    d.set_secret("leapmotor_pin_vin_b10", "1111")
    d.set_secret("leapmotor_pin_vin_t03", "2222")
    calls = []

    class _Api:
        operation_password = ""

        def lock_vehicle(self, vin):
            calls.append(("lock", vin, self.operation_password))
            return True

    c = _client(CARS)
    c._api = _Api()
    service = types.SimpleNamespace(publish_command_ack=lambda *a, **k: None)

    poller_main._handle_mqtt_command(c, service, d, "VIN_B10", "lock", None)
    poller_main._handle_mqtt_command(c, service, d, "VIN_T03", "lock", None)

    assert calls == [("lock", "VIN_B10", "1111"), ("lock", "VIN_T03", "2222")]
