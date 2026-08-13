"""Se l'account ha due auto, il setup le configura tutte e due.

🔴 Fino a oggi no: `detect_vehicle` chiedeva la lista al cloud e teneva `vehicles[0]`, buttando via
il resto. Il wizard mostrava una macchina, tu sceglievi il suo pacco e mettevi un PIN — e la
seconda auto entrava dopo, registrata dal poller, con il **default del modello** e nessuna domanda.
Per una C10 quel default è la RWD: una AWD sbaglia del 20%, una REEV di 2,4 volte, in silenzio.

Era noto e rimandato: nell'analisi che precedette il multi-veicolo stava come Tier 3, «setup wizard
— oggi hardcoda 1 auto». Il motore sa gestirne due da mesi; la porta d'ingresso no.

⚠️ Il taglio di batteria NON è deducibile: il cloud dice solo «C10», e C10 e B10 hanno più varianti
(più le REEV). È per questo che il wizard mostra una LISTA — e per questo va mostrata per OGNI auto.
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
import main


class _FakeVehicle:
    def __init__(self, vin, car_type):
        self.vin, self.car_type = vin, car_type


class _FakeApi:
    def __init__(self, vehicles):
        self._vehicles = vehicles

    def login(self):
        return None

    def get_vehicle_list(self):
        return self._vehicles

    def close(self):
        return None


def _patch_api(monkeypatch, vehicles):
    import command_client
    monkeypatch.setattr(command_client, "LeapmotorApiClient",
                        lambda **kw: _FakeApi(vehicles), raising=False)
    return command_client


def test_detect_returns_every_car_on_the_account(monkeypatch):
    cc = _patch_api(monkeypatch, [_FakeVehicle("VIN_B10", "B10"), _FakeVehicle("VIN_C10", "C10")])

    res = cc.detect_vehicle("u", "p", "1234")

    assert "error" not in res
    assert [v["vin"] for v in res["vehicles"]] == ["VIN_B10", "VIN_C10"]
    assert [v["car_type"] for v in res["vehicles"]] == ["B10", "C10"]


def test_each_car_carries_its_own_battery_options(monkeypatch):
    """La lista dei pacchi è del MODELLO: una B10 e una C10 non possono condividerla."""
    cc = _patch_api(monkeypatch, [_FakeVehicle("VIN_B10", "B10"), _FakeVehicle("VIN_C10", "C10")])

    cars = cc.detect_vehicle("u", "p", "1234")["vehicles"]

    b10 = [o["v"] for o in cars[0]["battery_options"]]
    c10 = [o["v"] for o in cars[1]["battery_options"]]
    assert b10 and c10 and b10 != c10
    assert "67.0" in c10        # C10 RWD (i valori sono stringhe: è la lista del wizard)
    assert "81.9" in c10        # C10 AWD — la variante che oggi nessuno chiedeva


def test_a_single_car_account_still_answers_the_old_shape(monkeypatch):
    """Chi ha una macchina sola non deve accorgersi di niente: il wizard di oggi legge vin/car_type."""
    cc = _patch_api(monkeypatch, [_FakeVehicle("VIN_B10", "B10")])

    res = cc.detect_vehicle("u", "p", "1234")

    assert res["vin"] == "VIN_B10" and res["car_type"] == "B10"
    assert len(res["vehicles"]) == 1


def test_an_account_with_no_cars_still_says_so(monkeypatch):
    cc = _patch_api(monkeypatch, [])

    assert "error" in cc.detect_vehicle("u", "p", "1234")


# ── il salvataggio: ogni auto porta via la SUA variante e il SUO PIN ────────────
def _setup_db(tmp_path, monkeypatch):
    import sqlite3
    import db as poller_db
    import db_reader
    path = str(tmp_path / "s.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path, db_reader


def test_the_wizard_saves_a_pack_and_a_pin_for_every_car(tmp_path, monkeypatch):
    """Il cuore: due auto trovate, due configurazioni salvate — non una sola più un default."""
    path, db_reader = _setup_db(tmp_path, monkeypatch)

    main.apply_setup_vehicles([
        {"vin": "VIN_B10", "car_type": "B10", "battery": "65.0", "is_reev": "0", "pin": "1111"},
        {"vin": "VIN_C10", "car_type": "C10", "battery": "81.9", "is_reev": "0", "pin": "2222"},
    ])

    cars = {v["vin"]: v for v in db_reader.get_vehicles()}
    assert set(cars) == {"VIN_B10", "VIN_C10"}
    assert cars["VIN_C10"]["capacity_kwh"] == 81.9      # AWD scelta, non il default 67,0
    assert cars["VIN_B10"]["capacity_kwh"] == 65.0
    assert db_reader.get_secret("leapmotor_pin_vin_c10", "") == "2222"
    assert db_reader.get_secret("leapmotor_pin_vin_b10", "") == "1111"


def test_a_reev_is_recorded_on_its_own_car(tmp_path, monkeypatch):
    """La variante REEV la sceglie l'utente nel wizard; il segnale 3235 la confermerà al primo poll,
    ma fino ad allora è questa scelta a decidere i numeri."""
    path, db_reader = _setup_db(tmp_path, monkeypatch)

    main.apply_setup_vehicles([
        {"vin": "VIN_B10", "car_type": "B10", "battery": "65.0", "is_reev": "0", "pin": "1111"},
        {"vin": "VIN_C10", "car_type": "C10", "battery": "28.4", "is_reev": "1", "pin": "2222"},
    ])

    assert db_reader.get_setting("is_reev_vin_c10", "") == "1"
    assert db_reader.get_setting("is_reev_vin_b10", "") == "0"


def test_every_configured_car_is_marked_as_such(tmp_path, monkeypatch):
    """Ciò che distingue «configurata» da «entrata da sola col default»: senza questo, nessuno
    può dire quali auto ha visto un essere umano."""
    path, db_reader = _setup_db(tmp_path, monkeypatch)

    main.apply_setup_vehicles([
        {"vin": "VIN_B10", "car_type": "B10", "battery": "65.0", "is_reev": "0", "pin": "1111"},
        {"vin": "VIN_C10", "car_type": "C10", "battery": "81.9", "is_reev": "0", "pin": "2222"},
    ])

    assert db_reader.get_setting("vehicle_setup_done_vin_c10", "") == "1"
    assert db_reader.get_setting("vehicle_setup_done_vin_b10", "") == "1"


def test_one_car_goes_through_the_same_door(tmp_path, monkeypatch):
    """Una macchina sola non è un caso speciale: è la lista con un elemento."""
    path, db_reader = _setup_db(tmp_path, monkeypatch)

    main.apply_setup_vehicles([
        {"vin": "VIN_B10", "car_type": "B10", "battery": "65.0", "is_reev": "0", "pin": "1111"},
    ])

    assert [v["vin"] for v in db_reader.get_vehicles()] == ["VIN_B10"]
    assert db_reader.get_secret("leapmotor_pin_vin_b10", "") == "1111"


def test_the_route_still_configures_the_single_car_that_posts_the_old_shape(tmp_path, monkeypatch):
    """Chi ha una macchina sola posta ancora vin/car_type/battery/pin: quella strada resta, e ora
    passa dalla stessa porta — così esiste UN solo punto che configura un'auto."""
    path, db_reader = _setup_db(tmp_path, monkeypatch)

    main.apply_setup_vehicles([{"vin": "VIN_B10", "car_type": "B10", "battery": "65.0",
                                "is_reev": "0", "pin": "1111"}])

    assert db_reader.get_setting("vehicle_setup_done_vin_b10", "") == "1"
    assert db_reader.get_secret("leapmotor_pin_vin_b10", "") == "1111"


def test_a_car_the_wizard_never_saw_is_not_marked_configured(tmp_path, monkeypatch):
    """La distinzione che serve al banner: entrata da sola ≠ configurata."""
    import sqlite3
    path, db_reader = _setup_db(tmp_path, monkeypatch)
    main.apply_setup_vehicles([{"vin": "VIN_B10", "car_type": "B10", "battery": "65.0",
                                "is_reev": "0", "pin": "1111"}])
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (vin, car_type, capacity_kwh) VALUES ('VIN_C10','C10',67.0)")
    con.commit(); con.close()

    assert db_reader.get_setting("vehicle_setup_done_vin_b10", "") == "1"
    assert db_reader.get_setting("vehicle_setup_done_vin_c10", "") == ""
