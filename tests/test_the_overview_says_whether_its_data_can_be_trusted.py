"""The status card says, in one sentence, whether the data on it can be trusted — and why not.

Discussion #300: for nine days the cloud refused an install's logins, and the Overview looked
exactly like a car asleep in the garage — "last seen 9 h ago", nothing else. The user spent a
week blaming an OTA update. Three questions were folded into that one figure and it answered
none of them: is the poller running, does the cloud let it in, and is the car sending anything.

The card now judges them in that order and prints the first that fails. Red only where MATE is
not fetching (the session refused, the fetch failing, no sign of the poller): those are the cases
the user can do something about, or at least must know about. A parked car with nothing new to
say stays grey — #130 and #178 already decided that a sleeping car is not an alarm — and the
amber mark is the existing #178 rule, not a new one.
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import main
from starlette.testclient import TestClient

VIN = "LFZB10LINK0000001"


@pytest.fixture
def web(tmp_path, monkeypatch):
    path = str(tmp_path / "link.db")
    poller = D.Database(path)
    poller._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'B10')", (VIN,))
    poller._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_setting("setup_complete", "1")
    return poller, TestClient(main.app)


def _position(db, frame_age_s, gear="P", speed=0.0, charging=0, row_age_s=0, vehicle_id=1):
    now = time.time()
    frame_ts = None if frame_age_s is None else int((now - frame_age_s) * 1000)
    db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, gear, speed_kmh, charging, frame_ts, "
        "latitude, longitude) VALUES (?, ?, 60, ?, ?, ?, ?, 45.0, 9.0)",
        (vehicle_id, datetime.fromtimestamp(now - row_age_s, timezone.utc).isoformat(), gear, speed, charging, frame_ts))
    db._conn.commit()


def _beat(db, age_s=0):
    db.set_setting("last_loop_ts", str(time.time() - age_s))


def _link(db, state, since_ago_s=0, reason=None, retry_in_s=None, bad_creds=False, vin=VIN):
    """What the poller writes: the session (ok / refused) in `poll_link`, this car's fetch
    (ok / empty / failed) in `poll_link_<vin>`; a car-side state implies a session that let it in."""
    body = {"state": state,
            "since": (datetime.now(timezone.utc) - timedelta(seconds=since_ago_s)).isoformat(),
            "reason": reason,
            "next_retry_ts": None if retry_in_s is None else time.time() + retry_in_s}
    if state in ("ok", "refused"):
        db.set_setting("poll_link", json.dumps({**body, "bad_creds": bad_creds}))
    if state in ("ok", "empty", "failed"):
        db.set_setting(f"poll_link_{vin.lower()}", json.dumps(body))
        if state != "ok":
            db.set_setting("poll_link", json.dumps({"state": "ok", "since": body["since"], "reason": None,
                                                    "next_retry_ts": None, "bad_creds": False}))


def _judge():
    return db_reader.data_link(db_reader.get_latest_status())


# ── the judgement ─────────────────────────────────────────────────────────────

def test_a_frame_under_five_minutes_old_is_up_to_date(web):
    db, _ = web
    _beat(db); _link(db, "ok"); _position(db, 12)
    out = _judge()
    assert out["state"] == "fresh" and out["red"] is False


def test_a_parked_car_with_nothing_new_is_grey_not_an_alarm(web):
    db, _ = web
    _beat(db); _link(db, "empty"); _position(db, 40 * 60)
    out = _judge()
    assert out["state"] == "no_new_data" and out["last_state"] == "parked"
    assert out["amber"] is False and out["red"] is False
    assert out["since_s"] == pytest.approx(40 * 60, abs=2)
    assert out["since_local"]


def test_a_frame_frozen_while_driving_is_the_amber_case(web):
    """The #178 rule, unchanged: the row is fresh, the frame is old, the car was moving."""
    db, _ = web
    _beat(db); _link(db, "ok"); _position(db, 40 * 60, gear="D", speed=50.0)
    out = _judge()
    assert out["state"] == "no_new_data"
    assert out["amber"] is True and out["last_state"] == "driving"


def test_a_charging_car_counts_as_moving_for_the_amber_mark(web):
    db, _ = web
    _beat(db); _link(db, "ok"); _position(db, 40 * 60, charging=1)
    out = _judge()
    assert out["amber"] is True


def test_a_frame_without_a_clock_has_an_unknown_age(web):
    db, _ = web
    _beat(db); _link(db, "ok"); _position(db, None)
    assert _judge()["state"] == "age_unknown"


def test_a_refused_session_is_red_with_the_date_and_the_next_attempt(web):
    db, _ = web
    _beat(db); _position(db, 9 * 86400, row_age_s=9 * 86400)
    _link(db, "refused", since_ago_s=9 * 86400, reason="RuntimeError: Leapmotor login failed",
          retry_in_s=28 * 60)
    out = _judge()
    assert out["state"] == "login_refused" and out["red"] is True
    assert out["retry_min"] == 28
    assert out["since_s"] == pytest.approx(9 * 86400, abs=2)
    assert out["bad_creds"] is False


def test_a_refusal_the_cloud_blamed_on_the_password_says_so(web):
    db, _ = web
    _beat(db); _position(db, 60)
    _link(db, "refused", reason="RuntimeError: account or password error", bad_creds=True)
    assert _judge()["bad_creds"] is True


def test_a_failing_fetch_carries_the_error_it_got(web):
    """"Fetching fails" alone leaves the user guessing; the message the poller stored says why."""
    db, _ = web
    _beat(db); _position(db, 60)
    _link(db, "failed", reason="ConnectionError: HTTPSConnectionPool Read timed out", retry_in_s=30)
    out = _judge()
    assert out["state"] == "fetch_failed"
    assert out["reason"] == "ConnectionError: HTTPSConnectionPool Read timed out"
    assert out["retry_min"] == 1
    assert out["car"]["frame"], "the banner names when the car last spoke"


def test_a_login_that_never_reached_the_cloud_fails_every_cars_fetch(web):
    """The poller restarted into a timeout at login: the session is neither ok nor refused, and
    every car's fetch is failing — not "age unknown", which is what a car without a clock says."""
    db, _ = web
    _beat(db); _position(db, 60); _link(db, "ok")
    db.set_setting("poll_link", json.dumps({"state": "failed", "since": datetime.now(timezone.utc).isoformat(),
                                            "reason": "ConnectionError: Read timed out",
                                            "next_retry_ts": time.time() + 10, "bad_creds": False}))
    out = _judge()
    assert out["state"] == "fetch_failed" and out["red"]
    assert out["reason"] == "ConnectionError: Read timed out" and out["retry_min"] == 1


def test_a_car_with_no_position_yet_is_judged_by_its_own_fetch(web):
    """Registered, never stored a frame — a car whose every fetch has failed since setup. The
    page shows it, so the page judges it, position row or none."""
    db, _ = web
    _beat(db)
    _link(db, "failed", reason="ConnectionError: Read timed out", retry_in_s=30)
    out = _judge()
    assert out["state"] == "fetch_failed" and "timed out" in out["reason"]
    assert out["car"]["frame"] is None


def test_a_refusal_carries_its_error_too(web):
    db, _ = web
    _beat(db); _position(db, 60)
    _link(db, "refused", reason="RuntimeError: Leapmotor login failed", retry_in_s=60)
    assert _judge()["reason"] == "RuntimeError: Leapmotor login failed"


def test_an_address_the_cloud_echoed_is_masked_and_a_long_error_is_cut(web):
    db, client = web
    _beat(db); _position(db, 60)
    _link(db, "refused", reason="RuntimeError: refused for user someone@example.com " + "x" * 300)
    out = _judge()
    assert "someone@example.com" not in out["reason"] and "…@…" in out["reason"]
    assert len(out["reason"]) == 200 and out["reason"].endswith("…")
    assert "someone@example.com" not in client.get("/api/link-pill").text


def test_a_silent_heartbeat_outranks_a_refused_session(web):
    """A refusal written by a process that has since stopped must not keep promising a retry."""
    db, _ = web
    _beat(db, age_s=20 * 60); _position(db, 60)
    _link(db, "refused", reason="RuntimeError: Leapmotor login failed", retry_in_s=28 * 60)
    out = _judge()
    assert out["state"] == "not_polling"
    assert out["last_error"] == "refused"
    assert out["reason"] == "RuntimeError: Leapmotor login failed"
    assert out["retry_min"] is None
    assert out["since_s"] == pytest.approx(20 * 60, abs=2)


def test_no_heartbeat_at_all_is_not_polling_with_no_date(web):
    db, _ = web
    _position(db, 60)
    out = _judge()
    assert out["state"] == "not_polling" and out["since_s"] is None


def test_the_heartbeats_grace_follows_the_parked_cadence(web):
    """An install polling every ten minutes is not told the poller is gone between two polls."""
    db, _ = web
    db.set_setting("poll_parked", "600")
    _beat(db, age_s=15 * 60); _link(db, "ok"); _position(db, 15 * 60)
    assert _judge()["state"] == "no_new_data"


def test_the_page_judges_the_car_it_shows_not_the_car_polled_last(web):
    """Two cars, two requests: the first keeps timing out, the second answers. The Overview of
    the first says so; the Overview of the second is fine — whichever was polled last."""
    db, _ = web
    other = "LFZB10LINK0000002"
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2, ?, 'B10')", (other,))
    db._conn.commit()
    _beat(db); _position(db, 60); _position(db, 12, vehicle_id=2)
    _link(db, "failed", reason="ConnectionError: Read timed out", retry_in_s=30)
    _link(db, "ok", vin=other)
    db_reader.set_active_vehicle(other)
    assert _judge()["state"] == "fresh"
    db_reader.set_active_vehicle(VIN)
    out = _judge()
    assert out["state"] == "fetch_failed" and "timed out" in out["reason"]


def test_the_demo_runs_no_poller_and_shows_no_tile(web, monkeypatch):
    db, client = web
    _position(db, 60)                      # no heartbeat at all — the demo is web only
    monkeypatch.setattr(main, "_IS_DEMO", True)
    assert 'id="link-pill"' not in client.get("/").text
    assert client.get("/api/link-pill").text == ""
    monkeypatch.setattr(main, "_IS_DEMO", False)
    assert 'data-link="not_polling"' in client.get("/api/link-pill").text


# ── the two renders agree ─────────────────────────────────────────────────────

def test_the_status_card_itself_stays_as_it_was(web):
    """The tile lives beside the heading; the card's 30-second refresh must not grow a copy."""
    db, client = web
    _beat(db); _position(db, 60)
    _link(db, "refused", reason="RuntimeError: Leapmotor login failed", retry_in_s=60)
    assert "data-link" not in client.get("/api/status-card").text


def test_both_renders_of_the_tile_carry_the_chain_and_the_banner(web):
    db, client = web
    _beat(db); _position(db, 9 * 86400, row_age_s=9 * 86400)
    _link(db, "refused", since_ago_s=9 * 86400, reason="RuntimeError: Leapmotor login failed",
          retry_in_s=28 * 60)
    for url in ("/", "/api/link-pill"):
        html = client.get(url).text
        assert 'data-link="login_refused"' in html, url
        assert 'data-link-chain="red-grey"' in html, url
        assert 'data-link-banner="login_refused"' in html, url
        assert "the Leapmotor cloud refuses the login" in html, url
        assert re.search(r"Next attempt: \d\d/\d\d \d\d:\d\d \(in 28 min\)\.", html), url   # the banner, line by line
        assert re.search(r"Last data from the car: \d\d/\d\d \d\d:\d\d \(9d ago\)\.", html), url
        assert 'data-link-error>RuntimeError: Leapmotor login failed<' in html, url


def test_a_parked_car_gets_grey_dots_with_the_facts_on_hover_and_no_sentence(web):
    """Nothing is wrong, so nothing is said; a hover on each dot tells what it knows."""
    db, client = web
    _beat(db); _link(db, "empty"); _position(db, 40 * 60)
    html = client.get("/api/link-pill").text
    assert 'data-link-chain="green-grey"' in html, "cloud fine, car quiet"
    assert "data-link-reason" not in html and "data-link-banner" not in html, "nothing wrong, no sentence"
    assert "Last frame:" in html and "State: Parked" in html, "the facts are on the car's hover"
    assert "Session: OK since" in html and "Last answer:" in html, "…and on the cloud's"
    assert "Poller running since:" in html and "Heartbeat:" in html, "…and on Mate's"
    assert len(re.findall(r'data-tip="', html)) >= 4, "a fact sheet on each word, and one on the icon"


def test_a_frame_frozen_while_driving_gets_the_amber_row(web):
    db, client = web
    _beat(db); _link(db, "ok"); _position(db, 40 * 60, gear="D", speed=50.0)
    html = client.get("/api/link-pill").text
    assert 'data-link-chain="green-amber"' in html
    assert re.search(r"Driving, nothing new since \d\d/\d\d \d\d:\d\d \(40m ago\)", html), "the same shape as every moment"
    assert "data-link-banner" not in html


def test_the_password_hint_appears_only_when_the_cloud_blamed_the_password(web):
    db, client = web
    _beat(db); _position(db, 60)
    _link(db, "refused", reason="RuntimeError: account or password error", bad_creds=True)
    html = client.get("/api/link-pill").text
    assert 'data-link-banner="login_refused"' in html
    assert "the password was rejected" in html and "Check the Leapmotor account" in html
    _link(db, "refused", reason="RuntimeError: Leapmotor login failed", bad_creds=False)
    html = client.get("/api/link-pill").text
    assert "the password was rejected" not in html and "Check the Leapmotor account" not in html


def test_every_moment_in_a_tooltip_is_the_clock_and_its_age(web):
    """One shape for every point in time — "27/09 02:29 (3h ago)" — so the three sheets read alike;
    what never happened says so instead of a date."""
    db, client = web
    _beat(db, age_s=2); _link(db, "empty", since_ago_s=3600); _position(db, 40 * 60)
    db.set_setting("poller_started_ts", str(time.time() - 5 * 3600))
    db._conn.execute("INSERT INTO poll_log (at, vehicle_id, kind, outcome) VALUES (?, 1, 'poll', 'empty')",
                     ((datetime.now(timezone.utc) - timedelta(seconds=12)).isoformat(),))
    db._conn.commit()
    tips = re.findall(r'data-tip="([^"]*)"', client.get("/api/link-pill").text)
    moment = r"\d\d/\d\d \d\d:\d\d \(\d+[smhd] ago\)"
    assert re.search(rf"Poller running since: {moment}\nHeartbeat: {moment}$", tips[1]), tips[1]
    assert re.search(rf"Session: OK since {moment}\nLast answer: {moment}\nLast failure: never$", tips[2]), tips[2]
    assert re.search(rf"Last frame: {moment}\nState: Parked$", tips[3]), tips[3]


def test_a_frame_without_a_clock_is_named_by_when_it_was_received(web):
    """No clock in the frame, so no age from the car — the hover says when the poller took it in."""
    db, client = web
    _beat(db); _link(db, "ok"); _position(db, None, row_age_s=90)
    html = client.get("/api/link-pill").text
    assert re.search(r"Last frame: received \d\d/\d\d \d\d:\d\d \(1m ago\) · no clock in the frame", html)
    assert "Last frame: never" not in html
