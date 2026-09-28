"""The Overview says which software the car runs, and whether an update is waiting.

The row was "OTA updates" and read "None" whenever the inbox had no update message — which on an
account the car is shared with is always, because such an account gets no vehicle notices at all.
"None" read as "the car is up to date". The cloud tells the version only to the account that owns
the car, so the row is "Software" now and says one of these, in this order:

  • the installed version → the one waiting — from the cloud, with the size and the notes;
  • the installed version, up to date — from the cloud;
  • "Unknown", with the reason beside it — "(shared car)", "(cloud refused)" — and the nuance in its
    tooltip; "update available" follows when the inbox holds an update message, which is what an
    account the car is shared with can get.
"""
import json
import re
import time
from datetime import datetime, timezone

import pytest

pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import main
from starlette.testclient import TestClient

VIN = "LFZB10SOFT0000001"
KEY = f"software_{VIN.lower()}"
UP_TO_DATE = {"state": "ok", "installed": "3.41.30", "installed_at_ms": 1790224725000,
              "latest": "3.41.30", "notes": "", "size_bytes": None}
WAITING = dict(UP_TO_DATE, latest="3.42.1", notes="Improvements\nCharging is smoother.",
               size_bytes=3 * 2**30 + 2**29)


@pytest.fixture
def web(tmp_path, monkeypatch):
    path = str(tmp_path / "software.db")
    poller = D.Database(path)
    poller._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'B10')", (VIN,))
    # the card is drawn from the last position; without one there is no card to read
    poller._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, gear, speed_kmh, charging, frame_ts, "
        "latitude, longitude) VALUES (1, ?, 60, 'P', 0, 0, ?, 45.0, 9.0)",
        (datetime.now(timezone.utc).isoformat(), int(time.time() * 1000)))
    poller._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_setting("setup_complete", "1")
    return TestClient(main.app)


def _row(client) -> str:
    """The Software row of the status card, and nothing around it — found by its label, not its icon."""
    html = client.get("/api/status-card").text
    m = re.search(r">[^<>]*(?:Software|Oprogramowanie)</span>.*?</div>", html, re.DOTALL)
    assert m, "the row is on the card"
    return m.group(0)


def _tip(row) -> str:
    m = re.search(r'data-tip="([^"]*)"', row)
    assert m, "the value carries a tooltip"
    return m.group(1)


def _set(software=None, inbox=False, language="en"):
    db_reader.set_setting("language", language)
    if software is not None:
        db_reader.set_setting(KEY, json.dumps(software))
    if inbox:
        db_reader.set_setting("ota_available", "1")
        db_reader.set_setting("ota_title", "Software update available")


def _tips(row) -> list:
    return re.findall(r'data-tip="([^"]*)"', row)


def test_the_row_is_called_software(web):
    _set(UP_TO_DATE)
    row = _row(web)
    assert "Software</span>" in row and "OTA updates" not in row


def test_a_waiting_update_names_both_versions_its_size_and_its_notes(web):
    _set(WAITING)
    row = _row(web)
    assert "⬆️ 3.41.30 → 3.42.1" in row and "text-amber-300" in row
    tip = _tip(row)
    assert "3.5 GB" in tip
    assert "Improvements" in tip and "Charging is smoother." in tip


def test_an_up_to_date_car_shows_its_version(web):
    _set(UP_TO_DATE)
    row = _row(web)
    assert "3.41.30 · up to date" in row
    assert "Installed" in _tip(row)


def test_the_cloud_answer_outranks_an_inbox_message(web):
    """An owner's inbox keeps the message of an update since installed; the version says it is done."""
    _set(UP_TO_DATE, inbox=True)
    row = _row(web)
    assert "up to date" in row and "update available" not in row and "⬆️" not in row


def test_a_shared_account_reads_unknown_and_is_told_why(web):
    _set({"state": "shared"})
    row = _row(web)
    assert "Unknown (shared car)" in row and "update available" not in row
    assert "only to the account that owns the car" in _tip(row)


def test_a_shared_account_with_an_inbox_message_sees_it_beside_unknown(web):
    _set({"state": "shared"}, inbox=True)
    row = _row(web)
    assert "Unknown (shared car)" in row and "⬆️ update available" in row
    assert any("Software update available" in t for t in _tips(row)), "the message title is in its tooltip"


def test_a_refusal_does_not_claim_the_car_is_shared(web):
    """Code 40 for a car the account lists as its own: the tooltip says what happened, not why."""
    _set({"state": "refused"})
    row = _row(web)
    assert "Unknown (cloud refused)" in row and "shared car" not in row
    assert "Code 40" in _tip(row) and "shared" not in _tip(row)


def test_nothing_known_yet_reads_unknown(web):
    _set(None)
    row = _row(web)
    assert "Unknown" in row and "shared car" not in row and "cloud refused" not in row
    assert "every six hours" in _tip(row)


def test_a_setting_with_no_version_is_not_a_version(web):
    _set({"state": "ok", "installed": None, "latest": None})
    assert "Unknown" in _row(web)


def test_the_row_speaks_the_ui_language(web):
    _set({"state": "shared"}, language="pl")
    row = _row(web)
    assert "Oprogramowanie</span>" in row and "Nieznane (auto udostępnione)" in row
    _set(UP_TO_DATE, language="pl")
    assert "3.41.30 · aktualne" in _row(web)
