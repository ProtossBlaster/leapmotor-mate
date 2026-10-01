"""Earlier months of cloud trips can be imported, once each, from September 2026 and not before.

01/10/2026, Silvio on a new MateDesktop (Windows): with "Import trips from Leapmotor cloud" on, only
that morning's trip came in, none of September's. The history worker asks the cloud from the first
day of the CURRENT month, so on an installation made on the 1st nothing earlier is ever asked: a
month that was never the current one while Mate ran stays out for good.

What the cloud holds was measured the same day, read-only, with the worker's own client and call:
June, July and August 2026 came back empty while Mate itself recorded 118, 197 and 134 trips in
them; 20/08 -> 01/09 empty; 01/09 -> 03/09 thirteen records, the first on 01/09; one request over
twelve months returned 204, September's 202 and October's 2. Silvio's rule: whoever wants them can
import the earlier months, starting from September 2026 and not before.

So the import card offers the months from September 2026 up to last month. The chosen month and
every one after it are asked once each, as whole months in the user's time zone; a month that came
back complete is not asked again, one that did not is asked at the next synchronization; and no
earlier month is asked while the import itself is off.
"""
import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import mate_api  # noqa: F401 — the runtime's own import path, as tests/test_history_account_scope.py
import cloud_import_policy as policy
import history_worker as worker

ROME = ZoneInfo("Europe/Rome")


class _Clock(datetime):
    """The worker's clock, held at one instant."""
    held = None

    @classmethod
    def now(cls, tz=None):
        return cls.held.astimezone(tz) if tz else cls.held


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    path = tmp_path / "history.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE vehicles(id INTEGER PRIMARY KEY, vin TEXT)")
        db.execute("INSERT INTO vehicles(vin) VALUES ('OWNER')")
    monkeypatch.setattr(worker, "DB", str(path))
    monkeypatch.setattr(worker, "connect_db", lambda: sqlite3.connect(path))
    monkeypatch.setattr(worker.crypto, "decrypt", lambda value: value)
    monkeypatch.setattr(worker, "datetime", _Clock)
    monkeypatch.setenv("TZ", "Europe/Rome")
    monkeypatch.setenv("LEAPMOTOR_USER", "user")
    monkeypatch.setenv("LEAPMOTOR_PASS", "password")
    state = SimpleNamespace(path=path, asked=[], imports=0, incomplete=set())

    def imported(db, importer):
        state.imports += 1
        return {"state": "ok"}

    monkeypatch.setattr(worker, "import_trips", imported)
    monkeypatch.setattr(worker, "migrate_charges", lambda db: {"inserted": 0})

    class API:
        def __init__(self, **kwargs):
            pass

        def get_vehicle_list(self):
            return [SimpleNamespace(vin="OWNER", is_shared=False)]

        def route(self, vin):
            return {"appCenter": "synthetic-origin"}

        def read(self, path, body, **kwargs):
            kind = path.split("/")[2]
            start = datetime.fromtimestamp(int(body["startTime"]), ROME).strftime("%Y-%m-%d %H:%M")
            end = datetime.fromtimestamp(int(body["endTime"]), ROME).strftime("%Y-%m-%d %H:%M")
            state.asked.append((kind, start, end))
            total = 3 if (kind, start[:7]) in state.incomplete else 0
            return {"data": dict(pageNum=int(body["pageNum"]), pageSize=int(body["pageSize"]),
                                 totalPage=0, total=total, list=[])}

    monkeypatch.setattr(worker, "NewAPIClient", API)
    return state


def _set(state, **values):
    with sqlite3.connect(state.path) as db:
        db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", list(values.items()))


def _sync(state, at):
    """One synchronization at `at`; the windows it asked, as (kind, first minute, last minute)."""
    state.asked = []
    _Clock.held = at
    worker.sync_once()
    return state.asked


OCTOBER_1ST = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
SEPTEMBER = [("mileage", "2026-09-01 00:00", "2026-10-01 00:00"),
             ("charge", "2026-09-01 00:00", "2026-10-01 00:00")]


def _past(asked):
    return [window for window in asked if window[1] < "2026-10-01"]


def test_without_a_choice_only_the_current_month_is_asked(cloud):
    _set(cloud, api_v2_import_cloud_trips="1")
    asked = _sync(cloud, OCTOBER_1ST)
    assert [(kind, start) for kind, start, _ in asked] == [("mileage", "2026-10-01 00:00"),
                                                          ("charge", "2026-10-01 00:00")]


def test_a_chosen_month_is_asked_whole_and_only_once(cloud):
    _set(cloud, api_v2_import_cloud_trips="1", api_v2_import_cloud_from="2026-09")
    assert _past(_sync(cloud, OCTOBER_1ST)) == SEPTEMBER
    assert _past(_sync(cloud, OCTOBER_1ST)) == [], "September came back complete: not asked again"


def test_nothing_before_september_2026_is_asked(cloud):
    """A month earlier than September 2026 (only a hand-edited setting can say so) starts there."""
    _set(cloud, api_v2_import_cloud_trips="1", api_v2_import_cloud_from="2026-06")
    assert _past(_sync(cloud, OCTOBER_1ST)) == SEPTEMBER


def test_the_earlier_months_wait_for_the_import_to_be_on(cloud):
    _set(cloud, api_v2_import_cloud_trips="0", api_v2_import_cloud_from="2026-09")
    assert _past(_sync(cloud, OCTOBER_1ST)) == []


def test_every_month_from_the_chosen_one_is_asked_and_none_before_it(cloud):
    _set(cloud, api_v2_import_cloud_trips="1", api_v2_import_cloud_from="2026-11")
    asked = _sync(cloud, datetime(2027, 1, 10, 12, 0, tzinfo=timezone.utc))
    assert sorted({start[:7] for _, start, _ in asked}) == ["2026-11", "2026-12", "2027-01"]
    assert ("mileage", "2026-12-01 00:00", "2027-01-01 00:00") in asked, "December closes on New Year"


def test_installed_in_january_one_choice_brings_every_month_in_the_first_synchronization(cloud):
    """Silvio, 01/10/2026: «installo Mate a gennaio, devo importarmi tutti i mesi volta per volta?»
    No: one choice, and the first synchronization asks September to December, each once."""
    _set(cloud, api_v2_import_cloud_trips="1", api_v2_import_cloud_from="2026-09")
    asked = _sync(cloud, datetime(2027, 1, 10, 12, 0, tzinfo=timezone.utc))
    assert sorted({start[:7] for _, start, _ in asked if start < "2027-01"}) == [
        "2026-09", "2026-10", "2026-11", "2026-12"]


def test_a_choice_made_once_brings_in_every_month_that_ends_without_choosing_again(cloud):
    """Silvio, 01/10/2026: «a gennaio devo ricaricare mese per mese a mano?» No. Chosen once in
    October, September comes in then, October once November has started, and in January the
    months not yet asked come in together, each once."""
    _set(cloud, api_v2_import_cloud_trips="1", api_v2_import_cloud_from="2026-09")
    months = lambda asked, before: sorted({start[:7] for _, start, _ in asked if start < before})
    assert months(_sync(cloud, OCTOBER_1ST), "2026-10") == ["2026-09"]
    assert months(_sync(cloud, datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)), "2026-11") == ["2026-10"]
    assert months(_sync(cloud, datetime(2027, 1, 10, 12, 0, tzinfo=timezone.utc)), "2027-01") == [
        "2026-11", "2026-12"]


def test_a_month_that_came_back_incomplete_is_asked_again_and_holds_nothing_else_up(cloud):
    _set(cloud, api_v2_import_cloud_trips="1", api_v2_import_cloud_from="2026-09")
    cloud.incomplete = {("mileage", "2026-09")}
    asked = _sync(cloud, OCTOBER_1ST)
    assert ("mileage", "2026-10-01 00:00", "2026-10-01 11:30") in asked, "the current month still read"
    assert cloud.imports == 1, "the trips already staged were still imported"
    cloud.incomplete = set()
    assert _past(_sync(cloud, OCTOBER_1ST)) == [SEPTEMBER[0]], "only the incomplete one, again"


@pytest.mark.parametrize("today, months", [
    (date(2026, 9, 30), []),
    (date(2026, 10, 1), [(2026, 9)]),
    (date(2027, 1, 10), [(2026, 9), (2026, 10), (2026, 11), (2026, 12)]),
])
def test_the_months_on_offer_start_in_september_2026_and_end_last_month(today, months):
    assert policy.past_months(today) == months


# ── the Settings card ─────────────────────────────────────────────────────────

@pytest.fixture
def web(tmp_path, monkeypatch):
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import db as D
    import db_reader
    import main
    from starlette.testclient import TestClient
    path = str(tmp_path / "web.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "today_local", lambda: date(2026, 10, 1))
    db_reader.set_setting("setup_complete", "1")
    db_reader.set_setting("language", "it")
    return db_reader, TestClient(main.app)


def test_the_card_offers_september_and_nothing_earlier(web):
    _, client = web
    page = client.get("/settings").text
    assert 'name="cloud_import_from"' in page
    assert ">Solo il mese in corso<" in page and ">Settembre 2026<" in page
    assert "Agosto 2026" not in page


def test_the_card_says_how_many_months_each_choice_brings(web, monkeypatch):
    """Silvio, 01/10/2026: «tra 5 mesi cosa vedo?» Each choice says what it adds. The count cannot
    sit inside the closed menu: at 1024 px the menu has 138 px and «Da settembre 2026 (6 mesi)»
    needs 182 (234 in Dutch), so it is a line under it that follows the choice."""
    db_reader, client = web
    monkeypatch.setattr(db_reader, "today_local", lambda: date(2027, 3, 10))
    db_reader.set_setting("api_v2_import_cloud_from", "2026-09")
    page = client.get("/settings").text
    assert 'data-span="In più 6 mesi: Settembre 2026 – Febbraio 2027"' in page
    assert 'data-span="In più 3 mesi: Dicembre 2026 – Febbraio 2027"' in page
    assert 'data-span="In più 1 mese: Febbraio 2027"' in page
    assert 'data-span="Nessun mese precedente."' in page
    assert '>In più 6 mesi: Settembre 2026 – Febbraio 2027</p>' in page, "the chosen one, said at load"


def test_the_card_saves_a_month_it_offers_and_nothing_else(web):
    db_reader, client = web
    client.post("/api/settings/cloud-import", data={"cloud_import_trips": "1", "cloud_import_from": "2026-09"})
    assert db_reader.get_setting("api_v2_import_cloud_from") == "2026-09"
    client.post("/api/settings/cloud-import", data={"cloud_import_trips": "1", "cloud_import_from": "2026-08"})
    assert db_reader.get_setting("api_v2_import_cloud_from") == "2026-09", "August is not on offer"
    client.post("/api/settings/cloud-import", data={"cloud_import_trips": "1", "cloud_import_from": ""})
    assert db_reader.get_setting("api_v2_import_cloud_from") == ""
