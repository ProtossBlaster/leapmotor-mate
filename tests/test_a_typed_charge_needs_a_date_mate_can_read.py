"""A charge typed in by hand needs a date Mate can read, and the Overview does not draw a missing SoC as 0 %.

#451 (@ebagnoli): the Overview's *Last charge* showed a DC charge of 19.8 kWh and 12.09 € with SoC 0% → 0%
and no duration, while he charges only at home on AC. It was a charge he had typed in, with the year
72026: the date box of a browser takes a year of five digits, `local_to_utc_iso` cannot read it (Python
stops at 9999) and handed the text back unchanged, and the form stored it. Charges are ordered by their
start as text, and "72026-…" sorts after every "2026-…", so that row was the latest charge for good.

The add form and the edit form refuse a date that cannot be read, as the CSV import already does. And the
Overview card says "—" for a SoC the charge does not carry, as the charge card does since #188.
"""
import re
import sqlite3

import db as D
import db_reader
import pytest

pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

from test_a_days_heading_sums_up_its_trips import _client


@pytest.fixture
def mate(tmp_path, monkeypatch):
    import main  # noqa: F401  (the app the client drives)
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    for key, value in (("setup_complete", "1"), ("timezone", "UTC")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return path, _client()


def _charges(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM charges ORDER BY id")]
    finally:
        con.close()


_FORM = {"time": "11:35", "energy": "19.81", "cost": "12.09", "charge_type": "DC"}


def test_a_charge_typed_with_a_year_mate_cannot_read_is_refused(mate):
    path, client = mate
    r = client.post("/api/charges/manual", data={**_FORM, "date": "72026-03-20"})
    assert r.status_code == 400
    assert _charges(path) == []


def test_a_charge_typed_with_a_date_mate_can_read_is_added(mate):
    path, client = mate
    r = client.post("/api/charges/manual", data={**_FORM, "date": "2026-03-20"})
    assert r.status_code == 200
    assert [c["started_at"] for c in _charges(path)] == ["2026-03-20T11:35:00+00:00"]


def test_an_end_mate_cannot_read_is_refused(mate):
    path, client = mate
    r = client.post("/api/charges/manual", data={**_FORM, "date": "2026-03-20",
                                                 "end_date": "72026-03-20", "end_time": "13:00"})
    assert r.status_code == 400
    assert _charges(path) == []


def test_an_edit_to_a_date_mate_cannot_read_is_refused_and_leaves_the_charge_as_it_was(mate):
    path, client = mate
    cid = db_reader.add_manual_charge("2026-03-20T11:35:00+00:00", 19.81, cost=12.09, charge_type="DC")
    before = _charges(path)
    r = client.post(f"/api/charges/{cid}/edit", data={**_FORM, "date": "72026-03-20"})
    assert r.status_code == 400
    assert _charges(path) == before


def _last_charge_soc(html):
    """The SoC line of the Overview's Last charge card."""
    card = html.split('id="last-charge"', 1)[-1] if 'id="last-charge"' in html else html
    m = re.search(r">\s*SOC\s*</span>\s*<span[^>]*>(.*?)</span>", card, re.S)
    assert m, "the Last charge card has no SoC line"
    return re.sub(r"<[^>]+>", "", m[1]).strip()


def test_the_last_charge_without_a_soc_says_so_instead_of_0_percent(mate):
    _, client = mate
    db_reader.add_manual_charge("2026-03-20T11:35:00+00:00", 19.81, cost=12.09, charge_type="DC")
    assert _last_charge_soc(client.get("/").text) == "—"


def test_a_real_0_percent_is_still_shown(mate):
    _, client = mate
    db_reader.add_manual_charge("2026-03-20T11:35:00+00:00", 2.0, charge_type="AC", start_soc=0.0, end_soc=12.0)
    assert _last_charge_soc(client.get("/").text) == "0% → 12%"


def _stored_before_the_fix(path):
    """The row as 4.27.0 and earlier stored it (#451's charge 13, typed before its type was a real one):
    the text as typed, no zone."""
    con = sqlite3.connect(path)
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh, charge_type,"
                " location_type, cost, cost_manual, manual_entry, reconstructed)"
                " VALUES (13,1,'72026-03-20T11:35:00','72026-03-20T11:35:00',19.81,'DC','MANUAL',12.09,1,1,0)")
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh, start_soc, end_soc)"
                " VALUES (14,1,'2026-11-04T13:16:00+00:00','2026-11-04T13:23:00+00:00',0.23,26.2,27.0)")
    con.commit()
    con.close()


def test_charges_lists_a_row_stored_with_such_a_year_above_its_calendar_whatever_the_month(mate):
    """It belongs to no month, so the calendar could not show it, and the Overview showed nothing else:
    the reader could neither correct nor delete it. It is listed above the calendar, with the date as
    stored, its edit form and its bin."""
    path, client = mate
    _stored_before_the_fix(path)
    for html in (client.get("/charges").text,
                 client.get("/api/charges/calendar", params={"year": 2026, "month": 3}).text,
                 client.get("/api/charges/calendar", params={"year": 2025, "month": 1}).text):
        parts = html.split('id="charges-undated"', 1)
        assert len(parts) == 2, "no block for the charges whose date cannot be read"
        block = parts[1].split("</section>", 1)[0]
        assert 'id="charge-card-13"' in block and "72026-03-20" in block
        assert "charges/13/edit" in block
        assert 'id="charge-card-14"' not in block


def test_without_such_a_row_there_is_no_block(mate):
    path, client = mate
    db_reader.add_manual_charge("2026-03-20T11:35:00+00:00", 19.81, cost=12.09, charge_type="DC")
    assert 'id="charges-undated"' not in client.get("/charges").text


def test_a_row_stored_with_such_a_year_can_be_deleted(mate):
    path, client = mate
    _stored_before_the_fix(path)
    assert client.delete("/api/charges/13").status_code == 200
    assert [c["id"] for c in _charges(path)] == [14]
    assert 'id="charges-undated"' not in client.get("/charges").text
    assert _last_charge_soc(client.get("/").text) == "26% → 27%"


def test_a_row_stored_with_such_a_year_is_found_by_a_search_and_can_be_corrected(mate):
    """What a reader who has one can do about it: a search by type lists it, with its edit form, and the
    edit puts it back among the dated charges — the Overview's last charge is the real one again."""
    path, client = mate
    _stored_before_the_fix(path)
    assert "19.8" in client.get("/").text                      # the row the Overview takes as the last
    found = client.get("/api/charges/search", params={"type": "MANUAL"})   # its bundle: "DC /MANUAL"
    assert found.status_code == 200 and 'id="charge-card-13"' in found.text
    assert "charges/13/edit" in found.text
    r = client.post("/api/charges/13/edit", data={**_FORM, "date": "2026-03-20"})
    assert r.status_code == 200
    assert [c["started_at"] for c in _charges(path) if c["id"] == 13] == ["2026-03-20T11:35:00+00:00"]
    assert _last_charge_soc(client.get("/").text) == "26% → 27%"
    assert 'id="charges-undated"' not in client.get("/charges").text
