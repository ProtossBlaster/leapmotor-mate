"""Diagnostics shows what the poller has been getting: a day of five-minute windows and a week
of counts, from the poll_log table — the figures discussion #300 was answered with by counting
lines of the poller log by hand.

Windows of wall clock, not polls: the cadence is 10–30 s and user-set, so a count of polls says
nothing on its own. The worst outcome inside a window wins it, and a window with no poll at all
is a gap. "No poll" minutes count CLOSED windows only, from the first row kept up to now — never
the future part of today, never the days before history began.
"""
import json
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest

pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import diagnostics
import main
from starlette.testclient import TestClient

VIN = "LFZB10POLLING0001"
# 01:00 UTC on a fixed day: today has run for an hour, so the closed windows are countable.
DAY = datetime(2026, 9, 24, tzinfo=timezone.utc)
NOW = (DAY + timedelta(hours=1)).timestamp()


@pytest.fixture
def web(tmp_path, monkeypatch):
    path = str(tmp_path / "polling.db")
    poller = D.Database(path)
    poller._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'B10')", (VIN,))
    poller._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_setting("setup_complete", "1")
    db_reader.set_setting("timezone", "UTC")
    return poller, TestClient(main.app)


def _poll(db, ago_s, outcome, frame_age_s=None):
    db._conn.execute(
        "INSERT INTO poll_log (at, vehicle_id, kind, outcome, frame_age_s) VALUES (?, 1, 'poll', ?, ?)",
        (datetime.fromtimestamp(NOW - ago_s, timezone.utc).isoformat(), outcome, frame_age_s))
    db._conn.commit()


def _login(db, ago_s, outcome, process):
    db._conn.execute(
        "INSERT INTO poll_log (at, kind, outcome, process) VALUES (?, 'login', ?, ?)",
        (datetime.fromtimestamp(NOW - ago_s, timezone.utc).isoformat(), outcome, process))
    db._conn.commit()


class _Strip(HTMLParser):
    """The cells of the polling strip: every <i> inside the [data-diag-polling] block."""

    def __init__(self):
        super().__init__()
        self.depth, self.cells = 0, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-diag-polling" in attrs:
            self.depth = 1
        elif self.depth:
            self.depth += 1
            if tag == "i" and "data-tip" in attrs and "flex-1" in (attrs.get("class") or ""):
                self.cells.append(attrs["data-tip"])

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1


def _strip_cells(html) -> list:
    p = _Strip()
    p.feed(html)
    return p.cells


def _week(db):
    """The week #300 would have shown, compressed into this morning."""
    _poll(db, 3000, "failed")                 # 00:10 — the first row kept
    _poll(db, 2000, "empty")                  # 00:26
    _poll(db, 1000, "answer", None)           # 00:43, a frame without a clock
    _poll(db, 400, "answer", 900)             # 00:53, an old frame …
    _poll(db, 395, "refused")                 # … and a refusal in the same window
    _poll(db, 60, "answer", 12)               # 00:59, current
    _login(db, 100, "ok", "web")
    _login(db, 100, "refused", "poller")
    _login(db, 86400 + 100, "refused", "web")  # yesterday


# ── the strip ─────────────────────────────────────────────────────────────────

def test_the_strip_has_a_cell_per_five_minutes_and_the_worst_outcome_wins(web):
    db, _ = web
    _week(db)
    strip = [c["cell"] for c in db_reader.polling_summary(now=NOW)["strip"]]
    assert len(strip) == 288
    assert strip[287] == "current"
    assert strip[286] == "refused", "an old frame and a refusal in one window: the refusal shows"
    assert strip[284] == "noclock"
    assert strip[281] == "empty"
    assert strip[278] == "failed"
    assert strip[0] == "gap" and strip[100] == "gap"


def test_one_current_frame_wins_a_window_over_the_re_served_ones(web):
    """A failure anywhere in the window shows; short of that, the best frame that arrived does."""
    db, _ = web
    _poll(db, 60, "answer", 900)
    _poll(db, 50, "answer", 12)
    _poll(db, 40, "answer", None)
    assert db_reader.polling_summary(now=NOW)["strip"][287]["cell"] == "current"
    _poll(db, 30, "empty")
    assert db_reader.polling_summary(now=NOW)["strip"][287]["cell"] == "empty"


def test_every_cell_says_its_window_in_words_for_a_hover(web):
    """Colour is never the only thing that says what a cell is."""
    db, client = web
    _poll(db, 395, "refused")
    last = db_reader.polling_summary(now=NOW)["strip"][286]
    assert (last["from"], last["to"], last["cell"]) == ("00:50", "00:55", "refused")
    # the page judges "the last 24 h" by the real clock, so its row is written at the real now
    db._conn.execute(
        "INSERT INTO poll_log (at, vehicle_id, kind, outcome) VALUES (?, 1, 'poll', 'refused')",
        (datetime.now(timezone.utc).isoformat(),))
    db._conn.commit()
    html = client.get("/api/polling-card").text          # the card's own body since 4.5.1
    cells = _strip_cells(html)
    assert len(cells) == 288 and any(tip.endswith("· session refused") for tip in cells)
    assert 'id="mate-tip"' in client.get("/settings").text, "the app's one tooltip box, from base.html"


@pytest.mark.parametrize("outcome, age, cell", [
    ("answer", 12, "current"), ("answer", 299, "current"), ("answer", 300, "old"),
    ("answer", None, "noclock"), ("empty", None, "empty"), ("failed", None, "failed"),
    ("refused", None, "refused"),
])
def test_a_row_becomes_one_word(outcome, age, cell):
    assert db_reader._poll_cell(outcome, age) == cell


# ── the week ──────────────────────────────────────────────────────────────────

def test_seven_local_days_of_counts_with_logins_split_by_process(web):
    db, _ = web
    _week(db)
    days = db_reader.polling_summary(now=NOW)["days"]
    assert [d["day"] for d in days][-2:] == ["2026-09-23", "2026-09-24"]
    assert len(days) == 7
    today, yesterday = days[-1], days[-2]
    assert today["polls"] == 6
    assert (today["current"], today["old"], today["noclock"], today["empty"],
            today["failed"], today["refused"]) == (1, 1, 1, 1, 1, 1)
    assert (today["login_ok_web"], today["login_refused_poller"],
            today["login_ok_poller"], today["login_refused_web"]) == (1, 1, 0, 0)
    assert yesterday["polls"] == 0 and yesterday["login_refused_web"] == 1


def test_no_poll_minutes_count_closed_windows_since_history_began(web):
    """History begins 00:10; by 01:00 there are ten closed windows from then, five of them
    without a poll — 25 minutes. Yesterday, before history began, counts nothing; and the part
    of today that has not happened yet counts nothing either."""
    db, _ = web
    _week(db)
    days = db_reader.polling_summary(now=NOW)["days"]
    assert days[-1]["no_poll_min"] == 25
    assert days[-2]["no_poll_min"] == 0
    assert all(d["no_poll_min"] == 0 for d in days[:-1])


def test_an_empty_table_is_all_gaps_and_zero_minutes(web):
    _, _ = web
    p = db_reader.polling_summary(now=NOW)
    assert {c["cell"] for c in p["strip"]} == {"gap"}
    assert all(d["no_poll_min"] == 0 and d["polls"] == 0 for d in p["days"])
    assert p["first_at"] is None


# ── the page and the bundle ───────────────────────────────────────────────────

def test_the_settings_page_draws_the_strip_and_the_table(web):
    """The card fetches its own body since 4.5.1 — Settings was paying for it on every load,
    open or not, and that was most of the slowness reported hours after 4.5.0 went out. What the
    card draws is unchanged, so this asks the endpoint the card asks."""
    db, client = web
    _week(db)
    settings = client.get("/settings").text
    assert "api/polling-card" in settings, "Settings no longer fetches the card at all"
    assert len(_strip_cells(settings)) == 0, "Settings still renders the strip inline"
    html = client.get("/api/polling-card").text
    assert len(_strip_cells(html)) == 288
    assert "Logins ok" in html and "poller / web" in html


def test_the_bundle_carries_the_week_and_the_link_state_without_the_raw_error(web, monkeypatch):
    db, _ = web
    _week(db)
    monkeypatch.setattr(db_reader.time, "time", lambda: NOW)     # the bundle picks its week by the clock
    db_reader.set_setting("poll_link", json.dumps({
        "state": "refused", "since": "2026-09-17T15:05:11+00:00",
        "reason": "RuntimeError: refused for user someone@example.com", "next_retry_ts": None,
        "bad_creds": False}))
    db_reader.set_setting("last_loop_ts", str(NOW))
    text = diagnostics.build_bundle("test", parts=("info",))
    assert "----- polling (last 7 days, local days) -----" in text
    assert "2026-09-24" in text
    assert "link         : refused since 2026-09-17T15:05:11+00:00 · RuntimeError · bad_creds=False" in text
    assert "someone@example.com" not in text, "the stored error may quote what was sent"
