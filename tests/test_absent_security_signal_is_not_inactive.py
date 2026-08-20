"""Un segnale che l'auto non manda non è «Inattivo» (#256, @ghuaywen-ai).

> *«Noto che lo Stato Sicurezza della C10 mostra sempre Inattivo, mentre la B10 mostra Attivo.
> È un limite della C10?»*

Sì, ed è misurato su tre auto — il segnale **1255** (`vehicleSecurityActive`):

| auto | segnali | 1255 |
|---|---|---|
| @ghuaywen-ai C10 | 77 | **assente** |
| @ebagnoli C10 (17 giorni continui) | 88 | **assente** |
| @michapr B10 | 98 | **presente**, 252 campioni, valori 0/1/2 |

Tutte e tre mandano 1256/1257/1258 (le posizioni del quadro): manca solo il 1255.

🔴 Il difetto è nostro, ed è la regola che avevamo già scritta — **assente non è zero**:

    security_active=int(sig.get("1255") or 0) != 0     # None → 0 → False → «Inattivo»

Mate non diceva «non lo so»: diceva **«Inattivo»**. Su uno stato di SICUREZZA quella non è
un'informazione mancante, è un'informazione falsa nel verso peggiore — chi legge quella riga capisce
che l'auto non è protetta. Silvio, 20/08: *«se il segnale non arriva si nasconde la riga»*.

⚠️ E il vecchio comportamento era **affermato da un test verde** (`test_vehicle_security.py`:
*«absent → False»*): la scelta era scritta, ed è quella che cambia qui.
→ [[signal-absent-is-not-signal-zero]] · [[feedback-a-green-test-can-assert-the-bug]]
"""
import pytest


def _sig(**kw):
    base = {"1258": 0, "1010": 0}
    base.update(kw)
    return base


# ── 1 · l'auto: assente ≠ zero ────────────────────────────────────────────────
def test_a_car_that_does_not_send_the_signal_says_nothing():
    """La C10. Prima tornava False, indistinguibile da un B10 con l'allarme spento."""
    import client
    assert client._parse_signal("VIN", _sig()).security_active is None


@pytest.mark.parametrize("raw,atteso", [(2, True), (1, True), (0, False)])
def test_a_car_that_sends_it_is_read_as_before(raw, atteso):
    """Il B10 manda 0/1/2 — misurato sul pacchetto di michapr. Nessuno di questi cambia."""
    import client
    assert client._parse_signal("VIN", _sig(**{"1255": raw})).security_active is atteso


# ── 2 · il database: NULL, non 0 ──────────────────────────────────────────────
def test_the_unknown_is_stored_as_null(tmp_path):
    """Se lo scriviamo 0 il difetto rinasce una riga più in là: la pagina legge il DB, non l'auto."""
    import db as D
    import db_reader

    class _Frame:
        """Il minimo che `save_position` maneggia: i numeri che confronta non possono essere None."""
        def __getattr__(self, n): return None
        vin = "VIN1"; car_type = "C10"
        soc = 55.0; speed_kmh = 0.0; odometer_km = 1000.0; range_km = 200.0
        timestamp_ms = 0; gear = "P"; vehicle_state = "parked"
        charging_status = 0; ac_port_mode = 0; fan_level = 0
        charge_current_a = 0.0; charge_voltage_v = 0.0
        windows_open_count = 0
        security_active = None                      # ← il punto del test

    pdb = D.Database(str(tmp_path / "t.db"))
    vid = pdb.ensure_vehicle("VIN1", "C10")
    pdb.save_position(vid, _Frame())
    row = pdb._conn.execute(
        "SELECT security_active FROM positions ORDER BY id DESC LIMIT 1").fetchone()
    assert row["security_active"] is None, f"scritto {row['security_active']!r} invece di NULL"


# ── 3 · la pagina: la riga sparisce ───────────────────────────────────────────
def _riga(status):
    """Il blocco della sicurezza, reso come lo rende la pagina."""
    import json
    import pathlib
    jinja2 = pytest.importorskip("jinja2", reason="serve jinja2")
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "web" / "templates" / "partials" / "status_card.html").read_text()
    # 🔑 il blocco va preso CON la sua guardia: estrarre solo il <div> interno rende il test cieco
    # proprio alla riga che deve verificare (ci sono cascato scrivendolo).
    # 🔑 il blocco va preso CON la sua guardia, e il suo {% endif %} è l'ULTIMO: dentro ce ne sono
    # altri due (le due ternarie del colore e del testo). Prendere il primo lascia il template
    # aperto e il test fallisce per colpa propria — ci sono cascato scrivendolo.
    marker = "{% if status.security_active is not none %}"
    if marker in src:
        start, i, apert = src.index(marker), src.index(marker), 0
        while True:
            nxt_if = src.find("{% if", i + 1)
            nxt_end = src.find("{% endif %}", i + 1)
            if nxt_if != -1 and nxt_if < nxt_end:
                apert += 1; i = nxt_if
            else:
                if apert == 0:
                    end = nxt_end + len("{% endif %}"); break
                apert -= 1; i = nxt_end
    else:
        start = src.rindex("<div", 0, src.index("🔒"))
        end = src.index("</div>", src.index("🔒")) + len("</div>")
    blocco = src[start:end]
    tr = json.loads((root / "web" / "locales" / "en.json").read_text())["translations"]
    return jinja2.Environment().from_string(blocco).render(status=status, t=lambda k: tr[k])


def test_the_row_is_gone_when_the_car_never_said(tmp_path):
    """Nessuna riga, non una riga che dice «Inattivo»."""
    out = _riga({"security_active": None})
    assert "Security" not in out and "Inactive" not in out, out


def test_the_row_is_there_when_the_car_did_say():
    """Il B10 non perde niente: attivo e non attivo restano tutti e due."""
    tr_on = _riga({"security_active": 1})
    tr_off = _riga({"security_active": 0})
    assert "Active" in tr_on
    assert "Inactive" in tr_off
