"""The €/kWh on a charge card names the kilowatt-hours underneath it.

@Tommy73LMB05, #346: an HPC session read **17,45 €**, **+41,2 kWh In der Batterie (DC)**,
**0,37 €/kWh** — and the Overview's *Ø-Preis in der Batterie* read **0,405**. Both figures are
right. 17,45 / 47,28 (what the column delivered, typed in) = 0,369; 17,45 / 41,2 (what reached the
pack) = 0,424, blended down to 0,405 because he plugged in at 16,6 %. Two correct numbers, one
money, two denominators — and the screen called both of them €/kWh.

The split is deliberate (`_billed_kwh` divides by what billed you, `_wac_blend` by what a trip
actually consumes). What was missing is the word saying which is which: the rate sat under
"+41,2 kWh In der Batterie" while dividing by 47,28, and there was no way to tell from the screen.

🔑 The basis is not a constant — it is `_billed_kwh`'s own branch (wallbox counter → the charger's
figure typed in → the battery kWh), so the label has to be read from the same place the number is,
or the two drift apart on the next change.
"""
import datetime as dt

import pytest

import db as D
import db_reader

VIN = "LVIN0000000000001"
STARTED = dt.datetime(2026, 9, 26, 16, 15, tzinfo=dt.timezone.utc)


def _page(tmp_path, monkeypatch, **charge):
    """The Charges page as it is served, with one charge on it."""
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    pytest.importorskip("fastapi")
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (VIN,))
    for key, value in (("timezone", "UTC"), ("setup_complete", "1"), ("language", "en"),
                       ("currency", "EUR")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    cols = dict(id=1, vehicle_id=1, started_at=STARTED.isoformat(),
                ended_at=(STARTED + dt.timedelta(minutes=24)).isoformat(),
                start_soc=16.6, end_soc=80.0, energy_added_kwh=41.2, cost=17.45,
                cost_manual=1, charge_type="DC", location_type="PUBLIC")
    cols.update(charge)
    c.execute(f"INSERT INTO charges ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
              tuple(cols.values()))
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    import main
    from starlette.testclient import TestClient
    # The card lives in the day panel the Charges page fetches by htmx — the very panel in his
    # screenshot ("26 Sep 2026 · 2 Ladevorgänge"), not in the page's own HTML.
    return TestClient(main.app).get("/api/charges/calendar/day?year=2026&month=9&day=26").text


def _rate_line(html):
    """The €/kWh line of the cost cell, as one string."""
    import re
    # Anchored on the cost cell's own id. The gross-kWh block under the energy card carries the
    # same inline style and comes FIRST in the page, and it says "delivered" all by itself — a
    # loose search matched that instead and reported the rate as labelled when it was not.
    cell = re.search(r'<div id="cost-1".*?(?=<div id="(?!cost-1)|\Z)', html, re.S)
    assert cell, "the charge card has no cost cell"
    m = re.search(r'<div style="font-size:10px;color:#94a3b8;font-weight:400">(.*?)</div>',
                  cell.group(0), re.S)
    assert m, "the cost cell shows no €/kWh line at all"
    # VISIBLE text only. The label's own tooltip explains the difference, so it names both
    # "delivered" and "battery" — asserted against the raw markup, every assertion below passes
    # whatever the label says, which is exactly how a wrong label survived its first test.
    return " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())


def test_a_typed_charger_figure_is_named_on_the_rate(tmp_path, monkeypatch):
    """His case: the column's own kWh, typed in. 17,45 / 47,28 — not the 41,2 printed beside it."""
    html = _page(tmp_path, monkeypatch, gross_kwh=47.28)
    line = _rate_line(html)
    assert "0.37" in line, f"the rate is not the one on his card: {line!r}"
    assert "delivered" in line.lower(), \
        f"the rate divides by the 47.28 kWh the charger delivered and does not say so: {line!r}"


def test_a_battery_only_charge_says_battery(tmp_path, monkeypatch):
    """No meter and nothing typed: the only figure that exists is what reached the pack, and the
    rate divides by THAT. Naming it 'delivered' there would be a second wrong word."""
    line = _rate_line(_page(tmp_path, monkeypatch))
    assert "0.42" in line, f"17.45 / 41.2 is 0.424, the card shows {line!r}"
    assert "batter" in line.lower(), f"the rate divides by the battery kWh and does not say so: {line!r}"


def test_a_wallbox_meter_is_named_too(tmp_path, monkeypatch):
    """A home charge measured on a counter bills on the counter, as _billed_kwh's first branch."""
    line = _rate_line(_page(tmp_path, monkeypatch, location_type="HOME", charge_type="AC",
                            ac_energy_kwh=47.28))
    assert "delivered" in line.lower() or "wallbox" in line.lower(), \
        f"the rate divides by the wallbox counter and does not say so: {line!r}"


def test_the_help_text_covers_a_typed_figure_too():
    """`blended_price_help` explained the gap for a charge "billed on a meter's kWh" — a home
    wallbox. His is an HPC session with the column's figure typed in: same arithmetic, same gap,
    and the sentence did not reach it."""
    import json
    import pathlib
    for locale in sorted(pathlib.Path("web/locales").glob("*.json")):
        text = json.loads(locale.read_text())["translations"]["blended_price_help"]
        low = text.lower()
        assert ("wallbox" in low or "meter" in low or "contator" in low or "contatore" in low
                or "zähler" in low or "contador" in low or "compteur" in low or "licznik" in low
                or "meter" in low), f"{locale.name}: the sentence names no billing basis at all"
        assert ("typed" in low or "entered" in low or "inserit" in low or "eingetragen" in low
                or "introducid" in low or "saisi" in low or "ingevoerd" in low or "wpisan" in low
                or "introduzid" in low), \
            (f"{locale.name}: blended_price_help still covers only a metered charge, not a figure "
             f"typed from the column — the case in #346")
