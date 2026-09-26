"""The real total paid can be typed on ANY charge, not only on one entered by hand (#308).

@adoewa's tariff is 0.3024 €/kWh; his last charge cost 18.10 and Mate printed 17.95. He asked
whether a charge recorded by Mate can be corrected afterwards, and was told that editing the cost
"only applies to charges entered by hand" — the ✏️ panel, which is indeed gated on `manual_entry`.

That answer left out the ✎ next to the type badge, which is a different control: since v3.16.0
(`fix: separate manual cost from charge type`) the badge partial includes `charge_cost_manual.html`
unconditionally, so every charge on the page carries a box for the total its owner actually paid —
pre-filled with the current figure, with a reset back to the computed price.

This test reads the rendered page, not the template: the claim being pinned is what the owner of a
MEASURED charge can see and press. It asks for the CARD — the Charges page's summary list carries
neither the badge nor the pencil; both arrive when a day of the calendar is opened (or a search is
run), which is where `charge_card.html` is drawn.
"""
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import db as poller_db
import db_reader
import pytest


def _day_card(cli) -> str:
    """The day drawer of the Charges calendar, which is where a charge is drawn as a CARD."""
    # The calendar uses the configured local date, not the stored UTC date.
    stamp = db_reader.get_charge(1)["started_at"]
    d = datetime.fromisoformat(stamp).astimezone(ZoneInfo("Europe/Rome"))
    return cli.get("/api/charges/calendar/day",
                   params={"year": d.year, "month": d.month, "day": d.day}).text


@pytest.fixture(params=["2026-09-24T12:00:00+00:00", "2026-09-24T22:30:00+00:00"])
def measured(tmp_path, monkeypatch, request):
    """One charge Mate recorded itself — manual_entry unset, cost computed from the tariff."""
    path = str(tmp_path / "i308.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'C10')")
    con.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh, "
        "charge_type, location_type, cost) VALUES (1, 1, ?, ?, 59.83, 'AC', 'HOME', 17.95)",
        (request.param, request.param))
    con.commit()
    con.close()
    db_reader.set_setting("timezone", "Europe/Rome")
    return path


def test_a_measured_charge_offers_the_box_for_the_total_actually_paid(measured):
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    pytest.importorskip("fastapi")
    import main
    from starlette.testclient import TestClient

    db_reader.set_setting("setup_complete", "1")
    html = _day_card(TestClient(main.app))

    # The pencil BY ITS OWN id: the type menu carries a second "✎ Manual" row that posts to the
    # same endpoint, so matching the endpoint alone passes with the pencil gone (measured — the
    # first version of this test did exactly that).
    assert 'id="cm-1"' in html, \
        "a charge Mate recorded itself shows no way to type the total that was really paid"
    assert re.search(r'id="cm-form-1".*?hx-post="api/charges/1/cost"', html, re.S), \
        "the pencil is drawn but posts nowhere"


def test_the_box_opens_on_the_figure_it_would_replace(measured):
    """Pre-filled on purpose: a bare submit CLEARS the typed total back to the computed one, so the
    box has to show what would be lost."""
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    pytest.importorskip("fastapi")
    import main
    from starlette.testclient import TestClient

    db_reader.set_setting("setup_complete", "1")
    html = _day_card(TestClient(main.app))

    assert re.search(r'id="cm-in-1"[^>]*value="17\.95"', html, re.S), \
        "the cost box does not open on the charge's current figure"
