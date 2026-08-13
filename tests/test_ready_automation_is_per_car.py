"""L'automazione «al Ready» è di un'AUTO, non dell'installazione.

Accende il clima quando la macchina si sveglia. Con due auto la configurazione era una sola: la
temperatura scelta per la B10 comandava anche la T03, e spegnerla su una la spegneva su entrambe.
È la forma 3 della lista dell'audit — un valore che è dell'auto, tenuto in chiave condivisa.

La forma della correzione è quella già usata per il token ABRP:
  · una chiave per VIN;
  · con UNA sola auto il valore condiviso continua a valere (non si spegne a chi funziona oggi);
  · quando compare la seconda auto, il condiviso passa a quella che c'era prima e la chiave
    condivisa si svuota — così lo stato è leggibile, senza regole nascoste;
  · da lì in poi un'auto senza configurazione propria non ne eredita nessuna.
"""
import json
import sqlite3

import db as poller_db
import db_reader


def _db(tmp_path, monkeypatch, vins):
    path = str(tmp_path / "r.db")
    d = poller_db.Database(path)
    con = sqlite3.connect(path)
    for i, vin in enumerate(vins, start=1):
        con.execute("INSERT INTO vehicles (id, vin) VALUES (?, ?)", (i, vin))
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return d


CFG_A = json.dumps({"enabled": True, "ac_temperature": 21})
CFG_B = json.dumps({"enabled": True, "ac_temperature": 26})


def test_each_car_keeps_its_own_settings(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ["VIN_A", "VIN_B"])
    db_reader.set_setting("ready_automation_vin_a", CFG_A)
    db_reader.set_setting("ready_automation_vin_b", CFG_B)

    db_reader.set_active_vehicle("VIN_A")
    assert db_reader.get_ready_automation_config()["ac_temperature"] == 21
    db_reader.set_active_vehicle("VIN_B")
    assert db_reader.get_ready_automation_config()["ac_temperature"] == 26


def test_with_one_car_the_shared_setting_still_applies(tmp_path, monkeypatch):
    """Chi ha una macchina sola e l'automazione accesa non deve accorgersi di niente."""
    _db(tmp_path, monkeypatch, ["VIN_A"])
    db_reader.set_setting("ready_automation", CFG_A)

    assert db_reader.get_ready_automation_config()["ac_temperature"] == 21


def test_a_second_car_does_not_inherit_the_first_ones(tmp_path, monkeypatch):
    """Il punto: con due auto, quella senza configurazione propria non prende quella dell'altra —
    accenderebbe il clima di una macchina su ordine di un'altra."""
    _db(tmp_path, monkeypatch, ["VIN_A", "VIN_B"])
    db_reader.set_setting("ready_automation_vin_a", CFG_A)

    db_reader.set_active_vehicle("VIN_B")
    assert db_reader.get_ready_automation_config().get("enabled") is False


def test_saving_writes_on_the_selected_car(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ["VIN_A", "VIN_B"])
    db_reader.set_active_vehicle("VIN_B")

    db_reader.save_ready_automation_config(
        {"enabled": "1", "ac_temperature": "26", "ac_mode": "cool"})

    assert json.loads(db_reader.get_setting("ready_automation_vin_b", "{}"))["ac_temperature"] == 26
    assert db_reader.get_setting("ready_automation_vin_a", "") == ""


# ── la migrazione, come per ABRP ────────────────────────────────────────────────
def test_the_shared_config_moves_onto_the_car_that_was_here_first(tmp_path, monkeypatch):
    d = _db(tmp_path, monkeypatch, ["VIN_A", "VIN_B"])
    db_reader.set_setting("ready_automation", CFG_A)

    moved = d.migrate_shared_ready_automation()

    assert moved == "VIN_A"
    assert db_reader.get_setting("ready_automation", "") == ""
    assert json.loads(db_reader.get_setting("ready_automation_vin_a", "{}"))["ac_temperature"] == 21


def test_the_migration_does_nothing_with_one_car(tmp_path, monkeypatch):
    d = _db(tmp_path, monkeypatch, ["VIN_A"])
    db_reader.set_setting("ready_automation", CFG_A)

    assert d.migrate_shared_ready_automation() == ""
    assert db_reader.get_setting("ready_automation", "") == CFG_A


def test_the_migration_is_idempotent_and_never_overwrites(tmp_path, monkeypatch):
    d = _db(tmp_path, monkeypatch, ["VIN_A", "VIN_B"])
    db_reader.set_setting("ready_automation", CFG_A)
    db_reader.set_setting("ready_automation_vin_a", CFG_B)      # l'auto ha già la sua

    d.migrate_shared_ready_automation()

    assert json.loads(db_reader.get_setting("ready_automation_vin_a", "{}"))["ac_temperature"] == 26
    assert db_reader.get_setting("ready_automation", "") == ""   # il residuo si butta
    assert d.migrate_shared_ready_automation() == ""


def test_the_poller_reads_the_car_it_is_polling(tmp_path, monkeypatch):
    """⚠️ Il poller gira su TUTTE le auto, non su quella selezionata nel web: deve chiedere la
    configurazione del VIN che sta interrogando, o applicherebbe a ognuna quella dell'altra."""
    import ready_automation
    d = _db(tmp_path, monkeypatch, ["VIN_A", "VIN_B"])
    d.set_setting("ready_automation_vin_a", CFG_A)
    d.set_setting("ready_automation_vin_b", CFG_B)

    assert ready_automation._config(d, "VIN_A")["ac_temperature"] == 21
    assert ready_automation._config(d, "VIN_B")["ac_temperature"] == 26
