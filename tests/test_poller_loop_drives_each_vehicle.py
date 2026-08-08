"""The poll loop, actually executed — which nothing in this suite had ever done.

Every other poller test reaches a helper. The 200-line loop inside `main()` — the thing that
records every trip and every charge of every install — was covered by nobody, so the refactor that
moves one car's state into a `VehicleContext` could have broken it with all 2321 tests green. That
is not a hypothetical: a green suite is exactly what made the last two defects invisible.

So the loop runs here against a stubbed cloud: a client that hands back canned frames and a real
database underneath. What it proves is small and load-bearing — the loop polls, the recorder
writes, and the per-car state (error counters, GPS signs, the raw-signal deltas) lives on the
context rather than in the enclosing function.
"""
import importlib.util
import pathlib
import sys


import db as D
import pytest


def _poller_main():
    """poller/main.py under its own name — a bare `import main` gets web/main.py, and the collision
    is silent. → [[mate-two-main-py-collision]]"""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_loop", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["poller_main_loop"] = mod
    spec.loader.exec_module(mod)
    return mod


PM = _poller_main()


def _frame(soc=60.0, odo=1000.0, gear="P", speed=0.0, ts=None):
    """One cloud frame.

    ⚠️ Built from the REAL `VehicleData`, field by field, rather than hand-listed on a namespace:
    the first version of this missed `window_fr_open` and the loop swallowed the AttributeError as
    a poll error, so the test read "the cloud was polled" while nothing was ever written. A frame
    that drifts from the dataclass is a test that quietly stops testing.
    """
    import dataclasses

    import client as _client
    kw = {}
    for f in dataclasses.fields(_client.VehicleData):
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
            continue                                  # the dataclass already has an answer
        kw[f.name] = _DEFAULTS.get(f.name, 0.0 if f.type in ("float", float) else None)
    kw.update(soc=soc, odometer_km=odo, gear=gear, speed_kmh=speed,
              timestamp_ms=ts or 1_760_000_000_000)
    return _client.VehicleData(**kw)


_DEFAULTS = {
    "latitude": 45.0, "longitude": 9.0, "range_km": 300, "charging_status": 0,
    "is_locked": True, "security_active": True, "charge_limit_percent": 80,
    "raw_signals": {}, "is_reev": False,
}


class _Stop(BaseException):
    """How a test ends the loop: not an Exception, so nothing in the loop can absorb it."""


class _Vehicle:
    def __init__(self, vin="VINLOOP0000000001", car_type="B10"):
        self.vin, self.car_type, self.year, self.abilities = vin, car_type, 2025, None
        self.is_shared = False


class _Client:
    """A cloud that answers, counts, and can be told to misbehave."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.calls = 0
        self.budget = 10**6      # rounds the test allows before it stops the loop
        self._vehicle = _Vehicle()
        self.closed = False

    def login(self):
        pass

    def relogin(self):
        pass

    def get_status(self):
        # Stop BEFORE the poll that would exceed the budget, so the rounds that did run are whole.
        if self.calls >= self.budget:
            raise _Stop
        self.calls += 1
        f = self._frames.pop(0) if self._frames else _frame()
        if isinstance(f, Exception):
            raise f
        return f

    def close(self):
        self.closed = True


@pytest.fixture
def loop(tmp_path, monkeypatch):
    """`main()` wired to a stub cloud and a real database, stopped after N polls."""
    path = str(tmp_path / "loop.db")
    monkeypatch.setenv("DB_PATH", path)

    def run(frames, rounds=2):
        client = _Client(frames)
        client.budget = rounds
        monkeypatch.setattr(PM, "LeapmotorMateClient", lambda **kw: client)

        # A fake clock, advanced only by the loop's own sleeps.
        #
        # ⚠️ Two things learned by getting them wrong. The end-of-round sleep is INTERRUPTIBLE —
        # `while True: time.sleep(min(5, remaining))` — so a 30-second wait is six calls, and
        # counting sleeps counts sixths of a round. And with the sleep stubbed out to nothing the
        # loop would spin against the real clock for thirty real seconds a round. So the sleep moves
        # a clock instead of taking time, and the round count comes from the polls themselves.
        clock = {"t": 1_760_000_000.0}
        monkeypatch.setattr(PM.time, "time", lambda: clock["t"])
        monkeypatch.setattr(PM.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
        # Everything the loop reaches out to that isn't the point of this test.
        for name in ("_maybe_check_ota", "_maybe_refresh_charge_schedule"):
            monkeypatch.setattr(PM, name, lambda *a, **k: None)
        monkeypatch.setattr(PM.energy_snapshots, "maybe_sample", lambda *a, **k: None)
        monkeypatch.setattr(PM.ready_automation, "maybe_trigger", lambda *a, **k: None)
        monkeypatch.setattr(PM, "_mqtt_tick", lambda db, c, d, s: None)
        monkeypatch.setattr(PM, "load_config", lambda db: {
            "username": "u", "password": "p", "pin": "1234",
            "cert_path": "/tmp/c.pem", "key_path": "/tmp/k.pem"})

        database = D.Database(path)
        database.set_setting("setup_complete", "1")
        database._conn.commit()
        database._conn.close()

        try:
            PM.main()
        except _Stop:
            pass
        return client, D.Database(path)

    return run


# ── the loop does its job ─────────────────────────────────────────────────────

def test_the_loop_polls_and_the_recorder_writes(loop):
    client, db = loop([_frame(), _frame(soc=59.0)], rounds=2)
    assert client.calls >= 2, "the cloud was polled once per round"
    rows = db._conn.execute("SELECT COUNT(*) n FROM positions").fetchone()["n"]
    assert rows >= 2, "every poll wrote a position"
    assert db._conn.execute("SELECT COUNT(*) n FROM vehicles").fetchone()["n"] == 1


def test_the_heartbeat_is_written_every_round(loop):
    """`/healthz` reads it. It has to be written even on a round that failed, which is why it sits
    outside the try — a detail easy to lose while moving code around."""
    _, db = loop([_frame()], rounds=2)
    assert float(db.get_setting("last_loop_ts", "0")) > 0


# ── the per-car state is on the context, not in the enclosing function ────────

def test_a_sleeping_car_backs_off_on_its_own_counter(loop, caplog):
    """Three "no live data" answers put THIS car into offline backoff. The counter belongs to the
    car: with a second one it must not be the sleeping car that backs off the one being driven.

    ⚠️ Asserted on what the counter PRODUCES — the retries counted out loud, then the one back-off
    warning — not merely on the loop having gone round. The first version checked the poll count
    alone and a mutation that deleted the increment entirely sailed straight through it.
    → [[feedback-a-green-test-can-assert-the-bug]]"""
    err = PM.EmptyStatusError("asleep")
    with caplog.at_level("INFO", logger="leapmotor_mate"):
        client, _ = loop([err, err, err, _frame()], rounds=4)
    text = caplog.text
    assert client.calls >= 4
    assert "retry 1/3" in text and "retry 2/3" in text, "each attempt is counted out loud"
    assert "backing off" in text, "the third one backs this car off"
    assert text.count("backing off") == 1, "and says so once, not on every cycle after"


def test_a_cloud_error_does_not_stop_the_loop(loop):
    """The round that failed still ends in a sleep, still writes the heartbeat, and the next round
    still polls — which is what "an error is isolated" has to mean before there are two cars."""
    client, db = loop([RuntimeError("cloud down"), _frame(), _frame()], rounds=3)
    assert client.calls >= 3
    assert float(db.get_setting("last_loop_ts", "0")) > 0


def test_the_context_holds_what_the_locals_held():
    """The shape of the refactor, asserted on the object rather than on the loop: one car's error
    counters, GPS signs and raw-signal memory live here, so a second car gets its own set."""
    ctx = PM.VehicleContext.__new__(PM.VehicleContext)
    for field in ("vehicle", "vehicle_id", "vin", "recorder", "persisted_signs",
                  "empty_status_count", "poll_error_count", "research_last_sig",
                  "interval", "next_due"):
        assert field in PM.VehicleContext.__slots__, f"{field} must live on the context"
    assert "recorder" in PM.VehicleContext.__slots__


def test_two_contexts_do_not_share_a_thing(tmp_path):
    """The whole point. Two cars, two of everything — proved by mutating one and reading the other,
    because a class attribute or a shared default would pass any test that only builds one."""
    database = D.Database(str(tmp_path / "two.db"))
    a = PM.VehicleContext(database, _Vehicle("VIN_A", "B10"), 1)
    b = PM.VehicleContext(database, _Vehicle("VIN_B", "T03"), 2)
    a.empty_status_count = 7
    a.poll_error_count = 4
    a.research_last_sig["3235"] = "1"
    a.persisted_signs["lon"] = -1.0
    a.next_due = 999.0
    assert (b.empty_status_count, b.poll_error_count) == (0, 0)
    assert b.research_last_sig == {} and b.persisted_signs == {}
    assert b.next_due == 0.0, "a fresh car is due immediately, not at the other one's deadline"
    assert a.recorder is not b.recorder
    assert (a.vin, b.vin) == ("VIN_A", "VIN_B")
