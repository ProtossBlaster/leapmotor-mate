"""Con due auto, il token ABRP dell'installazione non vale più per nessuna delle due.

ABRP ha **un token per veicolo** anche dal suo lato. La regola già scritta in `get_abrp_token` è che
un'auto non eredita MAI il token dell'altra — indovinare ricrea la mescolanza che quella funzione
esiste per impedire. Ma la ricaduta sul token **unico dell'installazione** lasciava aperta la stessa
porta da un'altra parte: due auto senza token proprio ci ricadono **tutte e due**, e posizione, SoC
e velocità di due macchine finiscono nello stesso veicolo ABRP, mescolate.

Succede sulle installazioni che vengono da prima del multi-veicolo, dove quel token condiviso esiste
davvero. È la stessa scelta che `kerniger/leapmotor-ha` ha preso nella sua 0.7.0-beta.1: il token
dell'account **non** si copia su ogni auto.

⚠️ Con UNA sola auto la ricaduta resta: lì non c'è niente da mescolare, e toglierla spegnerebbe ABRP
a chi funziona oggi.
"""
import sqlite3

import db as poller_db
import pytest


def _db(tmp_path, vins):
    d = poller_db.Database(str(tmp_path / "p.db"))
    con = sqlite3.connect(str(tmp_path / "p.db"))
    for i, vin in enumerate(vins, start=1):
        con.execute("INSERT INTO vehicles (id, vin) VALUES (?, ?)", (i, vin))
    con.commit(); con.close()
    return d


def test_one_car_still_uses_the_install_wide_token(tmp_path):
    """Chi ha una macchina sola e ABRP che funziona non deve accorgersi di niente."""
    d = _db(tmp_path, ["VIN_A"])
    d.set_secret("abrp_token", "condiviso")

    assert d.get_abrp_token("VIN_A") == "condiviso"


def test_one_car_prefers_its_own_token(tmp_path):
    d = _db(tmp_path, ["VIN_A"])
    d.set_secret("abrp_token", "condiviso")
    d.set_abrp_token("suo", "VIN_A")

    assert d.get_abrp_token("VIN_A") == "suo"


def test_two_cars_never_share_the_install_wide_token(tmp_path):
    """Il difetto: entrambe ricadevano sullo stesso token e finivano nello stesso veicolo ABRP."""
    d = _db(tmp_path, ["VIN_A", "VIN_B"])
    d.set_secret("abrp_token", "condiviso")

    assert d.get_abrp_token("VIN_A") == ""
    assert d.get_abrp_token("VIN_B") == ""


def test_with_two_cars_the_one_that_has_a_token_still_sends(tmp_path):
    """La correzione non spegne chi è configurato: chi ha il suo token continua, l'altra tace."""
    d = _db(tmp_path, ["VIN_A", "VIN_B"])
    d.set_secret("abrp_token", "condiviso")
    d.set_abrp_token("solo_di_A", "VIN_A")

    assert d.get_abrp_token("VIN_A") == "solo_di_A"
    assert d.get_abrp_token("VIN_B") == ""      # né il condiviso, né quello di A


def test_a_car_never_inherits_the_other_cars_token(tmp_path):
    """La regola che c'era già, tenuta ferma."""
    d = _db(tmp_path, ["VIN_A", "VIN_B"])
    d.set_abrp_token("solo_di_A", "VIN_A")

    assert d.get_abrp_token("VIN_B") == ""


def test_without_a_vin_nothing_is_guessed(tmp_path):
    """Chiamata senza VIN su un impianto a due auto: non si sceglie per conto dell'utente."""
    d = _db(tmp_path, ["VIN_A", "VIN_B"])
    d.set_secret("abrp_token", "condiviso")

    assert d.get_abrp_token("") == ""


# ── la spia, che non deve dire «attivo» sul silenzio ────────────────────────────
def test_the_indicator_follows_what_would_actually_be_sent(tmp_path, monkeypatch):
    """Seconda faccia dello stesso difetto: con due auto e solo il token condiviso il poller non
    manda niente, e una spia che leggesse quella chiave direbbe «attivo» sopra il silenzio."""
    import db_reader
    d = _db(tmp_path, ["VIN_A", "VIN_B"])
    d.set_secret("abrp_token", "condiviso")
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))

    # il vecchio criterio — «esiste la chiave condivisa?» — direbbe di sì proprio qui:
    assert bool(db_reader.get_secret("abrp_token", "")) is True
    assert db_reader.abrp_token_in_use() is False        # quello nuovo guarda cosa PARTIREBBE

    d.set_abrp_token("solo_di_A", "VIN_A")
    assert db_reader.abrp_token_in_use() is True


def test_with_one_car_the_shared_token_still_lights_it_up(tmp_path, monkeypatch):
    import db_reader
    d = _db(tmp_path, ["VIN_A"])
    d.set_secret("abrp_token", "condiviso")
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))

    assert db_reader.abrp_token_in_use() is True


# ── la migrazione, quando la seconda auto viene registrata ──────────────────────
def test_the_shared_token_moves_onto_the_car_that_was_here_first(tmp_path):
    """Il token condiviso non è «di tutti»: fu configurato quando l'auto era UNA, quindi è suo.
    Alla comparsa della seconda lo si scrive sul suo VIN e la chiave condivisa si svuota — così lo
    stato è leggibile senza regole nascoste, e il selettore che intanto è comparso dice il vero."""
    d = _db(tmp_path, ["VIN_A", "VIN_B"])           # A ha id 1: c'era prima
    d.set_secret("abrp_token", "condiviso")

    moved = d.migrate_shared_abrp_token()

    assert moved == "VIN_A"
    assert d.get_secret("abrp_token", "") == ""
    assert d.get_abrp_token("VIN_A") == "condiviso"
    assert d.get_abrp_token("VIN_B") == ""


def test_the_migration_does_nothing_with_a_single_car(tmp_path):
    d = _db(tmp_path, ["VIN_A"])
    d.set_secret("abrp_token", "condiviso")

    assert d.migrate_shared_abrp_token() == ""
    assert d.get_secret("abrp_token", "") == "condiviso"    # resta dov'è: non c'è niente da separare


def test_the_migration_never_overwrites_a_token_already_set(tmp_path):
    """Se la prima auto ha già il suo, il condiviso è un residuo: si butta, non si sovrascrive."""
    d = _db(tmp_path, ["VIN_A", "VIN_B"])
    d.set_secret("abrp_token", "vecchio")
    d.set_abrp_token("suo", "VIN_A")

    d.migrate_shared_abrp_token()

    assert d.get_abrp_token("VIN_A") == "suo"
    assert d.get_secret("abrp_token", "") == ""


def test_the_migration_is_idempotent(tmp_path):
    d = _db(tmp_path, ["VIN_A", "VIN_B"])
    d.set_secret("abrp_token", "condiviso")

    d.migrate_shared_abrp_token()
    assert d.migrate_shared_abrp_token() == ""      # la seconda volta non c'è più niente da spostare
    assert d.get_abrp_token("VIN_A") == "condiviso"


# ── l'avviso: il silenzio va detto ──────────────────────────────────────────────
def test_the_settings_page_names_the_cars_that_send_nothing(tmp_path, monkeypatch):
    """Senza questo, la seconda auto semplicemente non compare mai su ABRP e nessuno dice perché."""
    import db_reader
    d = _db(tmp_path, ["VIN_A", "VIN_B"])
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))
    d.set_abrp_token("solo_di_A", "VIN_A")

    assert db_reader.abrp_cars_without_token() == ["VIN_B"[-6:]]


def test_with_one_car_nothing_is_claimed(tmp_path, monkeypatch):
    """Con una macchina sola il token dell'installazione la copre: nessun avviso da dare."""
    import db_reader
    _db(tmp_path, ["VIN_A"])
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))

    assert db_reader.abrp_cars_without_token() == []
