"""T03 A/C full-OFF — solved by @derekzoli, and it was the SHAPE of the payload, not the value (#67).

Open across the whole ecosystem for months: kerniger/leapmotor-ha#28, markoceri/leapmotor-api#9.
Every integration sent `operate=off` **bare** or `operate=close`, and the T03 ignores both — the cloud
answers `code:0` either way, which is why a log could never tell anyone apart. On 06-07/08/26
@derekzoli tested on his own T03 and, in his words:

    "So on the T03 it's not the value of `operate` that matters, it's the shape of the payload:
     it needs `off`, but only with the other fields present."

🔑 And he verified it the only way that counts: re-reading the vehicle status ~8 s later and watching
`acSwitch` go false, with the A/C actually stopping. Not an ACK.
→ [[feedback-a-successful-call-is-not-a-correct-result]]

Our own hunt (`web/t03_offtest.py`, 7 candidates, all dead) searched the grid in the wrong direction:
we held `operate` at `close`/`manual` and varied mode, fan, recirculation and invented keys. He held
the body still and moved `operate`. Candidate 0 of ours is byte-for-byte his `close pieno`.
"""

import json
import pathlib

import command_client as cc
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The exact body he verified on-car. Held here as the test's own literal — deriving it from the code
# under test would assert nothing.
VERIFIED = {"circle": "out", "mode": "wind", "operate": "off", "position": "all",
            "temperature": "26", "windlevel": "3", "wshld": "0"}


class _FakeApi:
    def __init__(self):
        self.calls, self.ac_off_calls, self.ac_switch_calls = [], 0, []

    def _remote_control(self, *, vin, action, cmd_content):
        self.calls.append((action, json.loads(cmd_content)))
        return {"code": 0}

    def ac_off(self, vin):
        self.ac_off_calls += 1        # the operate=close path — must NOT be used on the T03 any more

    def ac_switch(self, vin, params=None):
        self.ac_switch_calls.append(params)


class _FakeSession:
    def __init__(self):
        self.api = _FakeApi()

    def execute(self, fn):
        fn(self.api, "VIN")
        return True, "ok"


@pytest.fixture
def web(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr(cc, "_session", fake)
    monkeypatch.setattr(db_reader, "get_latest_status", lambda: {})
    return fake


# ── the web command path ──────────────────────────────────────────────────────

def test_the_t03_off_sends_the_body_he_verified(web, monkeypatch):
    monkeypatch.setattr(cc, "_session_car_type", lambda: "T03")
    cc.ac_off()
    action, body = web.api.calls[-1]
    assert action == "ac_on", "the whole climate is cmd 170; the library action name is ac_on"
    assert body == VERIFIED


def test_the_t03_no_longer_uses_operate_close(web, monkeypatch):
    """🔴 The regression that matters: `api.ac_off()` IS `operate=close`, which he measured as
    accepted-and-ignored. Sending both would look harmless and leave the car running."""
    monkeypatch.setattr(cc, "_session_car_type", lambda: "T03")
    cc.ac_off()
    assert web.api.ac_off_calls == 0
    assert web.api.ac_switch_calls == []


@pytest.mark.parametrize("car", ["B10", "C10", "B05", ""])
def test_every_other_model_keeps_the_path_confirmed_on_car(web, monkeypatch, car):
    """The B10 IGNORES the full-payload form and works on the bare one — the exact mirror image. This
    fix must not touch it: `operate=off` bare drives signal 1938 to 0, confirmed on-car 06/06/26."""
    monkeypatch.setattr(cc, "_session_car_type", lambda: car)
    cc.ac_off()
    assert web.api.ac_switch_calls == [{"operate": "off"}]
    assert web.api.calls == [], "no cmd-170 body for a car that wants the bare switch"


# ── the Home Assistant path ───────────────────────────────────────────────────

def test_the_home_assistant_button_sends_the_same_body():
    """⚠️ Two copies of one payload, in two trees that cannot import each other — the page and Home
    Assistant would otherwise come to disagree about the same car, silently.

    Compares the two VALUES, not the two source texts: the first version of this test grepped for the
    literal and went red the moment either file wrapped it across two lines, which says nothing about
    what the car receives. → [[feedback-gate-a-feature-find-every-copy]]"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("poller_main_t03off", ROOT / "poller" / "main.py")
    pmain = importlib.util.module_from_spec(spec)      # under its own name — a bare `main` is the WEB's
    spec.loader.exec_module(pmain)
    assert json.loads(pmain.T03_AC_OFF_BODY) == VERIFIED
    assert pmain.T03_AC_OFF_BODY == cc.T03_AC_OFF_BODY, "the page and Home Assistant must agree"


def test_the_dead_hunt_page_is_gone():
    """`web/t03_offtest.py` was marked `TEMPORARY (#67) — remove once cracked`. It is cracked, and its
    seven candidates are all known-dead: leaving it would send the only T03 owner we have down seven
    blind alleys."""
    assert not (ROOT / "web" / "t03_offtest.py").exists()
    assert not (ROOT / "web" / "templates" / "t03_offtest.html").exists()
    main = (ROOT / "web" / "main.py").read_text()
    assert "t03_offtest" not in main and "t03-offtest" not in main


def test_derekzoli_is_credited_where_the_payload_lives():
    """He solved a problem open across the whole ecosystem, from our own report, and brought it back
    to us. His name belongs next to the bytes, not only in the changelog."""
    for path in ("web/command_client.py", "poller/main.py"):
        assert "derekzoli" in (ROOT / path).read_text(), f"{path} does not say where this came from"
