"""OTA-update detection (client.check_ota): the account inbox is the only automatic "update
available" channel, matched by title keywords (stopgap until a real OTA message pins its
msg_type). The vehicle-sharing message must NOT be mistaken for an update."""
import types

import client as C


def _client_with(titles):
    api = types.SimpleNamespace(
        get_message_list=lambda page_no=1, page_size=20: types.SimpleNamespace(
            messages=[types.SimpleNamespace(title=t, message="", send_time=1780296848000)
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
