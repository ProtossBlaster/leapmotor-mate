"""Il fuso «Automatico» viene INCHIODATO al nome vero, una volta sola.

Auto non era sbagliato: era **non registrato**. `_local_tz` ripiegava sull'orologio del container
mentre l'impostazione restava vuota, quindi una ricarica digitata o importata veniva ancorata a un
orologio che nessuno aveva nominato — e `repair_manual_charge_timezones` si rifiuta (giustamente) di
girare senza un fuso scelto, quindi non poteva nemmeno rimetterla a posto. Se il container girava su
un fuso che non era né UTC né quello vero dell'utente, lo scarto restava dentro per sempre.
"""
import db as D            # schema del poller (crea settings/charges + migrazioni)
import db_reader


def _fresh(tmp_path, monkeypatch, env_tz=None):
    """DB su tmp_path, come gli altri test: CI-safe, nessun DB ambientale."""
    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    if env_tz is None:
        monkeypatch.delenv("TZ", raising=False)
    else:
        monkeypatch.setenv("TZ", env_tz)
    db_reader._TZ_CACHE["key"] = "\x00"


def test_auto_becomes_the_zone_it_was_already_resolving_to(tmp_path, monkeypatch):
    """Il container gira su Roma: l'impostazione passa da '' a 'Europe/Rome'. Niente si sposta —
    e' esattamente il fuso che era gia' in uso; cambia solo che adesso e' scritto."""
    _fresh(tmp_path, monkeypatch, "Europe/Rome")
    assert db_reader.get_setting("timezone", "") == ""
    assert db_reader.pin_auto_timezone() == "Europe/Rome"
    assert db_reader.get_timezone() == "Europe/Rome"


def test_an_explicit_choice_is_never_overwritten(tmp_path, monkeypatch):
    """Chi ha gia' scelto non deve vedersi sostituire la scelta dall'orologio del container."""
    _fresh(tmp_path, monkeypatch, "Europe/Rome")
    db_reader.set_timezone("Asia/Kuala_Lumpur")
    assert db_reader.pin_auto_timezone() == ""
    assert db_reader.get_timezone() == "Asia/Kuala_Lumpur"


def test_it_runs_once_and_then_stays_out_of_the_way(tmp_path, monkeypatch):
    """Dopo il primo giro l'utente puo' tornare su Auto di proposito: la migrazione non deve
    ripiombargli addosso al riavvio successivo."""
    _fresh(tmp_path, monkeypatch, "Europe/Rome")
    assert db_reader.pin_auto_timezone() == "Europe/Rome"
    db_reader.set_timezone("")                       # l'utente rimette Auto deliberatamente
    assert db_reader.pin_auto_timezone() == ""       # gia' fatto: non si ripete
    assert db_reader.get_timezone() == ""


def test_a_container_with_no_zone_is_utc_and_says_so(tmp_path, monkeypatch):
    """Docker nudo: niente TZ. Il valore onesto e' UTC — ed e' proprio quello che l'utente deve
    VEDERE nel wizard, perche' quasi mai e' dove si trova davvero."""
    _fresh(tmp_path, monkeypatch, None)
    monkeypatch.setattr(db_reader.Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError))
    monkeypatch.setattr(db_reader.Path, "resolve", lambda *a, **k: db_reader.Path("/etc/localtime"))
    assert db_reader.detected_tz_name() == "UTC"


def test_a_bogus_TZ_does_not_become_the_pinned_zone(tmp_path, monkeypatch):
    """Un TZ inventato non deve finire nell'impostazione: set_timezone lo scarterebbe tornando ad
    Auto, e la migrazione si segnerebbe come fatta lasciando il buco aperto per sempre."""
    _fresh(tmp_path, monkeypatch, "Mars/Olympus_Mons")
    monkeypatch.setattr(db_reader.Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError))
    monkeypatch.setattr(db_reader.Path, "resolve", lambda *a, **k: db_reader.Path("/etc/localtime"))
    assert db_reader.detected_tz_name() == "UTC"
    assert db_reader.pin_auto_timezone() == "UTC"
    assert db_reader.get_timezone() == "UTC"


def test_pinning_unblocks_the_repair(tmp_path, monkeypatch):
    """Il motivo per cui la migrazione gira PRIMA della riparazione all'avvio: la riparazione si
    ferma quando il fuso non e' stato scelto, quindi un'installazione lasciata su Auto non poteva
    mai far raddrizzare le sue vecchie righe digitate."""
    _fresh(tmp_path, monkeypatch, "Europe/Rome")
    assert db_reader.repair_manual_charge_timezones() == 0    # su Auto si tira indietro
    db_reader.pin_auto_timezone()
    assert db_reader.get_setting("timezone", "").strip() != ""   # ora ha una risposta
