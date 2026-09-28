"""OTA-update detection (client.check_ota): the account inbox is the only automatic "update
available" channel, matched by title keywords (stopgap until a real OTA message pins its
msg_type). The vehicle-sharing message must NOT be mistaken for an update."""
import time
import types

import client as C

_NOW_MS = int(time.time() * 1000)     # a notice ages out after a month, so the fixtures are dated today


def _client_with(titles):
    api = types.SimpleNamespace(
        get_message_list=lambda page_no=1, page_size=20: types.SimpleNamespace(
            messages=[types.SimpleNamespace(title=t, message="", send_time=_NOW_MS)
                      for t in titles]))
    c = C.LeapmotorMateClient.__new__(C.LeapmotorMateClient)   # skip the login-y __init__
    c._api = api
    return c


def test_ota_message_detected():
    for title in ("Aggiornamento software disponibile", "Software update available",
                  "Mise à jour du logiciel", "OTA upgrade", "Firmware update"):
        res = _client_with([title]).check_ota()
        assert res["ota"] is True and res["title"] == title, title


def test_sharing_message_is_not_ota():
    # The real message in Silvio's inbox — must be ignored, never shown as an update.
    # `ok`/`scanned` distinguish "read the inbox, nothing to update" from "couldn't read it" (#156).
    assert _client_with(["Condivisione veicolo"]).check_ota() == {"ok": True, "scanned": 1, "ota": False}
    assert _client_with(["Vehicle shared by owner"]).check_ota() == {"ok": True, "scanned": 1, "ota": False}
    assert _client_with([]).check_ota() == {"ok": True, "scanned": 0, "ota": False}


def test_picks_the_ota_among_others():
    res = _client_with(["Condivisione veicolo", "Aggiornamento software disponibile"]).check_ota()
    assert res["ota"] is True and "Aggiornamento" in res["title"] and res["scanned"] == 2


def test_unreadable_inbox_is_distinct_from_empty(monkeypatch):
    """An endpoint error must be ok=False (not the same as an empty inbox), so the poller keeps
    the last known value and the log/diagnostics can say the inbox couldn't be read — the case
    that used to look identical to "no update" on the Overview (#156, Malaysia C10)."""
    def _boom(*a, **k):
        raise RuntimeError("cloud down")
    c = C.LeapmotorMateClient.__new__(C.LeapmotorMateClient)
    c._api = types.SimpleNamespace(get_message_list=_boom)
    res = c.check_ota()
    assert res == {"ok": False}
    # ...and it's genuinely distinct from a successfully-read empty inbox:
    assert _client_with([]).check_ota()["ok"] is True


# ── The match must be tight enough to drive a phone notification (#277) ──────────────────────
# Exposing this flag on MQTT turns every false positive into a push at 3am, so the keyword list
# stopped being a bare substring scan: "ota" matched inside *nota* and *quota*, and a lone
# "upgrade"/"aggiornamento"/"mise à jour" matched membership offers and terms-of-service notices.
# Generic update words now only count next to a software/vehicle word; the acronyms match as words.

def _is_ota(title: str) -> bool:
    return bool(_client_with([title]).check_ota().get("ota"))


def test_a_word_that_merely_contains_ota_is_not_an_update():
    for title in ("Nota di servizio sul tuo veicolo", "Quota parcheggio in scadenza",
                  "Your Toyota trade-in offer", "Nota informativa"):
        assert _is_ota(title) is False, title


def test_a_generic_update_word_alone_is_not_an_update():
    for title in ("Upgrade your membership plan", "Aggiornamento condizioni di servizio",
                  "Mise à jour des conditions générales", "Update your payment method",
                  "Aktualisierung unserer Datenschutzerklärung"):
        assert _is_ota(title) is False, title


def test_the_real_notices_still_match_in_every_locale():
    for title in ("OTA update available", "FOTA task ready", "Firmware update for your vehicle",
                  "Software update available", "Software-Update verfügbar",
                  "Aggiornamento del software disponibile", "Aggiornamento sistema veicolo",
                  "Mise à jour du logiciel disponible", "Mise à jour véhicule",
                  "Software-Aktualisierung verfügbar", "Fahrzeug-Update verfügbar",
                  "Actualización de software disponible", "Atualização do software",
                  "Aktualizacja oprogramowania pojazdu", "Vehicle update available"):
        assert _is_ota(title) is True, title


def test_the_body_counts_too_not_only_the_title():
    api = types.SimpleNamespace(get_message_list=lambda page_no=1, page_size=20:
                                types.SimpleNamespace(messages=[types.SimpleNamespace(
                                    title="Leapmotor", message="Your firmware update is ready",
                                    send_time=_NOW_MS)]))
    c = C.LeapmotorMateClient.__new__(C.LeapmotorMateClient)
    c._api = api
    assert c.check_ota()["ota"] is True


# ── A notice is news for a month, not for ever ───────────────────────────────────────────────
# The scan looks at the twenty newest messages and reports the first that matches, however old.
# An update message from before Mate was installed would hold the flag ON — and the Home
# Assistant entity with it — until twenty newer messages had pushed it out of the page.

def _client_dated(pairs):
    api = types.SimpleNamespace(get_message_list=lambda page_no=1, page_size=20: types.SimpleNamespace(
        messages=[types.SimpleNamespace(title=t, message="", send_time=st) for t, st in pairs]))
    c = C.LeapmotorMateClient.__new__(C.LeapmotorMateClient)
    c._api = api
    return c


def test_a_notice_older_than_a_month_does_not_keep_the_flag_on(monkeypatch):
    now_ms = 1_790_000_000_000
    monkeypatch.setattr(time, "time", lambda: now_ms / 1000)
    old = now_ms - 31 * 86400 * 1000
    fresh = now_ms - 29 * 86400 * 1000
    assert _client_dated([("Software update available", old)]).check_ota()["ota"] is False
    assert _client_dated([("Software update available", fresh)]).check_ota()["ota"] is True


def test_a_notice_without_a_date_still_counts(monkeypatch):
    """The date is what ages a notice out; a message the cloud sent without one is not thrown
    away for that — a false OFF here is the silence the entity existed to end."""
    monkeypatch.setattr(time, "time", lambda: 1_790_000_000.0)
    assert _client_dated([("Software update available", None)]).check_ota()["ota"] is True
