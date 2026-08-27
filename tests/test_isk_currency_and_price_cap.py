"""ISK currency + no hard 9.99/kWh price cap (discussion #265, @alloutnow).

Iceland prices electricity in tens of ISK/kWh, so the currency has to exist AND the charging-price
fields must not refuse a value above 9.99 — a cap that already blocked HUF/JPY/KRW (all `dec:0`,
already in the list) as much as ISK. The cap lived only in the browser (costs.html `max`), never on
the server, so these tests guard the currency table, the zero-decimal formatting, and the template.
"""
import pathlib

import pytest


def test_isk_is_a_supported_currency():
    import db_reader
    assert "ISK" in db_reader.CURRENCIES, "Icelandic Króna missing from the currency list"
    isk = db_reader.CURRENCIES["ISK"]
    assert isk["dec"] == 0                      # no minor unit — totals show whole krónur
    assert isk["pos"] == "after"                # "1.234 kr."
    assert isk["symbol"] and "kr" in isk["symbol"].lower()
    assert "Icelandic" in isk["name"]


def test_isk_amount_keeps_at_least_two_decimals(monkeypatch):
    """ISK has zero ISO minor units, but Mate never rounds a figure on screen: a total keeps at least
    two decimals (Silvio's full-precision rule) instead of collapsing to whole krónur."""
    import db_reader
    import main
    monkeypatch.setattr(db_reader, "get_currency", lambda: db_reader.CURRENCIES["ISK"])
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    out = main._money(250)
    assert "250.00" in out and "kr" in out      # two decimals kept, not the whole "250"


def test_charge_price_fields_have_no_hardcoded_999_cap():
    """The €/kWh inputs (base per-type in the template, time-of-use bands in the JS) must not carry a
    max="9.99" — it blocks every high-value currency. min/step stay, so 0 and decimals still hold."""
    import db_reader
    src = (pathlib.Path(db_reader.__file__).resolve().parent / "templates" / "costs.html").read_text()
    assert 'max="9.99"' not in src, "static base-price input still caps at 9.99"
    assert 'max: "9.99"' not in src, "time-of-use band input (JS) still caps at 9.99"
    # the fields keep their floor and step — this is only about the ceiling
    assert 'min="0"' in src and 'step="0.01"' in src


def test_server_stores_a_high_kwh_price(tmp_path, monkeypatch):
    """No server-side cap: a 22/kWh Icelandic tariff round-trips through the store unchanged."""
    import db as poller_db
    import db_reader
    path = str(tmp_path / "p.db")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    poller_db.Database(path).close()
    db_reader.update_charge_price("price_home_kwh", 22.0)
    assert db_reader.get_charge_prices()["price_home_kwh"] == 22.0
