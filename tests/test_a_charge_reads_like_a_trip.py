"""A charge in the day drawer, and in the search results, is a row that opens, as a trip is a row that leads
to its page. The row says the times, the type, the duration, where it happened, the battery change, the kWh
and the cost. The rest of the card — the energy spelled out, the chart, the note on one line with the way to
edit it, the actions — is the body under it, which a click on the row opens. Expand all beside the heading
opens every row of the list.

3 July: a charge with a note, 40 → 60 %, 10 kWh, €5; one without a note. 5 July: one more charge.
"""
import re

import db as D
import db_reader
import pytest

pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

from test_a_days_heading_sums_up_its_trips import _client

_CHARGES = [(1, "03T08:00", "03T09:00", 10.0, 5.0, "Shaded spot, reliable"),
            (2, "03T17:00", "03T18:00", 7.0, 3.0, None),
            (3, "05T09:00", "05T11:00", 20.0, None, None)]


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'UTC')")
    for i, start, end, kwh, cost, note in _CHARGES:
        c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,"
                  " cost, note, duration_min, max_power_kw, charge_type, location_type)"
                  " VALUES (?,1,?,?,40,60,?,?,?,60,7.2,'AC','HOME')",
                  (i, f"2026-07-{start}:00+00:00", f"2026-07-{end}:00+00:00", kwh, cost, note))
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return _client()


def _drawer(client, **params):
    return client.get("/api/charges/calendar/day", params={"year": 2026, "month": 7, **params}).text


def _card(html, cid):
    """The row and the body of one charge, as markup."""
    m = re.search(rf'<details data-charge-id="{cid}".*?</details>', html, re.DOTALL)
    assert m, f"no card for charge {cid}"
    row, body = m.group(0).split("</summary>", 1)
    return row, body


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_the_row_says_what_a_trip_row_says(client):
    row, _ = _card(_drawer(client, day=3), 1)
    text = _text(row)
    assert "08:00 → 09:00" in text
    assert "⏱ 1h 00m · 7.2 kW peak · AC" in text
    assert "40.0% → 60.0% (+20.0%)" in text                  # the battery change, as a trip row prints it
    assert "+10.0 kWh" in text and "5.00 €" in text and "/kWh" in text
    assert 'id="charge-type-1"' in row and 'id="cost-1"' in row and 'id="loc-1"' in row and 'id="place-1"' in row, \
        "the editors the routes redraw by id stay in the row"


def test_the_note_is_one_line_of_the_body_with_the_way_to_edit_it(client):
    """A note reads on one line of the body's foot, with ✏️ beside it; without one the foot offers to add one.
    The row carries no note: it is read after the row is opened."""
    html = _drawer(client, day=3)
    row, body = _card(html, 1)
    assert "📝" not in _text(row)
    assert "📝 Shaded spot, reliable" in _text(body)
    assert " hidden" not in re.search(r'<div id="cnote-line-1"[^>]*>', body).group(0)
    assert re.search(r'<button type="button" id="cnote-edit-1"[^>]*>✏️</button>', body)
    row, body = _card(html, 2)
    assert re.search(r'<div id="cnote-line-2"[^>]*hidden[^>]*>📝 <span></span></div>', body), "an empty line, hidden"
    assert re.search(r'<button type="button" id="cnote-edit-2"[^>]*>📝 Add a note</button>', body)


def test_the_rest_of_the_card_is_the_body_under_the_row(client):
    row, body = _card(_drawer(client, day=3), 1)
    for piece in ('<textarea id="cnote-1" name="note"', "In battery (DC)", 'id="pchart-1"', 'hx-delete="api/charges/1"'):
        assert piece in body and piece not in row, piece
    assert "charge_profile" not in body and "📈" not in body, "the chart has no link of its own"
    # The order: the energy, the chart, then the foot with the note and the actions; the note's field closed.
    assert body.index("In battery (DC)") < body.index('id="pchart-1"') < body.index('id="cnote-line-1"') < body.index('hx-delete="api/charges/1"')
    assert re.search(r'<form id="cnote-form-1" class="hidden', body), "the note's field opens on purpose"


def test_a_saved_note_answers_trimmed_and_escaped(client):
    """The save answers with the note as stored, which the page writes into the body's line: trimmed, and
    escaped for the attribute it travels in."""
    said = client.post("/api/charges/2/note", data={"note": '  Bay "B" <near> the café  '}).text
    assert said.startswith('<span data-saved-note="Bay &quot;B&quot; &lt;near&gt; the café">✓ ')


@pytest.mark.parametrize("where,params", [
    ("day", {"year": 2026, "month": 7, "day": 3}),
    ("range", {"year": 2026, "month": 7, "day": 3, "to_day": 5}),
    ("search", {"q": "Shaded"}),
])
def test_expand_all_heads_every_list_of_rows(client, where, params):
    path = "/api/charges/search" if where == "search" else "/api/charges/calendar/day"
    html = client.get(path, params=params).text
    boxes = re.findall(r'<span data-charges-expand.*?</button>\s*</span>', html, re.DOTALL)
    assert len(boxes) == 1, "one pair for the whole list, ahead of its rows"
    assert html.index(boxes[0]) < html.index("<details data-charge-id")
    buttons = re.findall(r'<button type="button" data-charges-all="(\w+)"( hidden)?.*?</button>', boxes[0], re.DOTALL)
    assert [(a, bool(h)) for a, h in buttons] == [("open", False), ("close", True)], "Collapse all waits for an open row"
    assert re.sub(r"<[^>]+>|\s+", " ", boxes[0]).split() == ["▾", "Expand", "all", "▴", "Collapse", "all"]


def test_an_empty_search_has_no_expand_all(client):
    html = client.get("/api/charges/search", params={"q": "nothing like this"}).text
    assert "data-charges-expand" not in html
