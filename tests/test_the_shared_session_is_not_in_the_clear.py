"""La sessione condivisa sta cifrata, come tutti gli altri segreti.

`session_share` è la sola cosa in Mate che scrive un segreto **saltando `db_reader`**: si apre una
connessione sua e scrive `json.dumps(blob)` diretto nella tabella `settings`. Dentro quel blob ci
sono il token di sessione, il token di rinnovo e — in base64 — il certificato e la **chiave privata**
dell'account. Tutto il resto (token HA, ABRP, chiavi dei geocodificatori, PIN) passa da `set_secret`
e finisce cifrato.

E il backup del database è una copia byte per byte del file, quindi quel blob ci finisce dentro. La
pagina dell'esportazione, però, dice all'utente:

    «Il backup del database contiene anche le credenziali **cifrate**»

Per quella riga non era vero. Non è un buco nella serratura: è il **cartello sulla porta** che dice
una cosa diversa da com'è, ed è su quel cartello che l'utente decide se mandare il file a qualcuno.
→ [[feedback-two-numbers-one-word]]

Le tre cose che questa correzione NON deve rompere, tutte provate qui sotto:
 1. chi ha già il blob in chiaro deve continuare a funzionare (`crypto.decrypt` lascia passare il
    testo in chiaro apposta), e va ricifrato da solo al salvataggio successivo;
 2. una chiave sbagliata o persa non deve dare in pasto al cloud del testo illeggibile — meglio
    nessuna sessione e un accesso nuovo. È la lezione della #227, dove un `enc:v1:…` fu spedito
    come password e il cloud rispose «limite tentativi raggiunto»;
 3. web e poller devono restare **identici**: sono due copie dello stesso file.
"""
import json
import sqlite3

import pytest

import crypto
import session_share


@pytest.fixture
def archivio(tmp_path, monkeypatch):
    """Un DB vero con la sola tabella settings, e la chiave di cifratura in una cartella usa e getta.

    ⚠️ Servono anche cert e chiave su disco: `_restore` si tira indietro se non li trova, e senza di
    loro il test sarebbe rosso per colpa del banco invece che del difetto."""
    cert = tmp_path / "acc.crt"
    chiave = tmp_path / "acc.key"
    cert.write_bytes(b"-----BEGIN CERTIFICATE-----\nfinto\n")
    chiave.write_bytes(b"-----BEGIN PRIVATE KEY-----\nCHIAVE-PRIVATISSIMA\n")
    _Api.account_cert_file = str(cert)
    _Api.account_key_file = str(chiave)
    path = tmp_path / "t.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(session_share, "_db_path", lambda: str(path))
    monkeypatch.setenv("MATE_SECRET_KEY", "prova-di-collaudo")
    crypto._f.cache_clear() if hasattr(crypto._f, "cache_clear") else None
    return path


class _Api:
    """Il minimo che `_save` legge: gli attributi della sessione, senza file di certificato."""
    user_id = "u-1"
    token = "TOKEN-SEGRETISSIMO"
    refresh_token = "REFRESH-SEGRETISSIMO"
    device_id = "dev-1"
    sign_ikm = "ikm"
    sign_salt = "salt"
    sign_info = "info"
    account_cert_file = ""      # riempiti dalla fixture con due file veri
    account_key_file = ""


def _grezzo(path):
    con = sqlite3.connect(str(path))
    row = con.execute("SELECT value FROM settings WHERE key='shared_session'").fetchone()
    con.close()
    return row[0] if row else None


def test_the_token_is_not_readable_in_the_stored_row(archivio):
    """Il difetto in una riga: chi apre il backup non deve leggere il token a occhio nudo."""
    session_share._save(_Api())
    grezzo = _grezzo(archivio)
    assert grezzo, "il salvataggio non ha scritto niente"
    assert "TOKEN-SEGRETISSIMO" not in grezzo, "il token di sessione è in chiaro nel database"
    assert "REFRESH-SEGRETISSIMO" not in grezzo, "il token di rinnovo è in chiaro nel database"
    assert "CHIAVE-PRIVATISSIMA" not in grezzo, "la chiave privata dell'account è in chiaro"
    assert crypto.is_encrypted(grezzo), f"la riga non è cifrata: {grezzo[:60]}"


def test_what_was_saved_can_be_read_back(archivio):
    """Cifrare senza saper rileggere sarebbe peggio del difetto: ogni avvio farebbe un accesso nuovo."""
    session_share._save(_Api())
    api = types_ns()
    assert session_share._restore(api) is True, "la sessione cifrata non si rilegge"
    assert api.token == "TOKEN-SEGRETISSIMO"
    assert api.refresh_token == "REFRESH-SEGRETISSIMO"


def test_a_blob_written_in_the_clear_still_works_and_is_re_encrypted(archivio):
    """Chi aggiorna ha già il blob in chiaro: deve continuare a funzionare **senza** un nuovo accesso,
    e il salvataggio successivo lo mette al sicuro da sé. Nessuna migrazione, nessun intervento."""
    import time
    vecchio = {"user_id": "u-1", "token": "TOKEN-SEGRETISSIMO", "refresh_token": "REFRESH-SEGRETISSIMO",
               "device_id": "dev-1", "sign_ikm": "ikm", "sign_salt": "salt", "sign_info": "info",
               "account_cert_file": _Api.account_cert_file,
               "account_key_file": _Api.account_key_file, "ts": time.time()}
    con = sqlite3.connect(str(archivio))
    con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('shared_session',?)",
                (json.dumps(vecchio),))
    con.commit()
    con.close()

    api = types_ns()
    assert session_share._restore(api) is True, "il blob vecchio in chiaro non si legge più"
    assert api.token == "TOKEN-SEGRETISSIMO"

    session_share._save(_Api())                       # il primo salvataggio dopo l'aggiornamento
    assert "TOKEN-SEGRETISSIMO" not in _grezzo(archivio), "non è stato ricifrato"


def test_an_unreadable_blob_means_no_session_not_garbage(archivio, monkeypatch):
    """La lezione della #227: se la chiave non c'è più, meglio «nessuna sessione» che passare al
    cloud del testo cifrato scambiandolo per una credenziale."""
    session_share._save(_Api())
    monkeypatch.setattr(crypto, "decrypt", lambda v: "")      # chiave persa
    api = types_ns()
    assert session_share._restore(api) is False
    assert getattr(api, "token", None) in (None, ""), "ha comunque scritto qualcosa dentro l'api"


def test_the_two_copies_stay_identical():
    """web/ e poller/ sono due copie dello stesso file: correggerne una sola rimetterebbe il segreto
    in chiaro dal processo che non è stato toccato. → [[feedback-gate-a-feature-find-every-copy]]"""
    import pathlib
    radice = pathlib.Path(__file__).resolve().parents[1]
    a = (radice / "web" / "session_share.py").read_text()
    b = (radice / "poller" / "session_share.py").read_text()
    assert a == b, "le due copie di session_share.py hanno preso strade diverse"


def types_ns():
    import types
    return types.SimpleNamespace()
