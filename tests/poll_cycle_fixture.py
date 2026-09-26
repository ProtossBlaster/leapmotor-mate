"""One poll cycle, and one startup login, under test.

Shared by the tests of what the poller writes about its own cloud link (poll_log, poll_link, the
heartbeat). Each test module loads `poller/main.py` under its own module name — a bare
`import main` gets web/main.py — and builds its fixtures from these helpers, so the wiring that
keeps a cycle away from the network lives in one place.
"""
import dataclasses
import importlib.util
import pathlib
import sys

import db as D

NOW = 1_760_000_000.0
VIN = "VINPOLLCYCLE00001"


def poller_main(name: str):
    """poller/main.py as a module called `name`."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def frame(ts_ms, vin=VIN):
    """One cloud frame built from the real VehicleData, so a field the dataclass grows is not
    silently missing here (the loop test learned that the hard way)."""
    import client as _client
    defaults = {"latitude": 45.0, "longitude": 9.0, "range_km": 300, "charging_status": 0,
                "is_locked": True, "security_active": True, "charge_limit_percent": 80,
                "raw_signals": {}, "is_reev": False}
    kw = {}
    for f in dataclasses.fields(_client.VehicleData):
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
            continue
        kw[f.name] = defaults.get(f.name, 0.0 if f.type in ("float", float) else None)
    kw.update(vin=vin, soc=60.0, odometer_km=1000.0, gear="P", speed_kmh=0.0, timestamp_ms=ts_ms)
    return _client.VehicleData(**kw)


class Client:
    """A cloud told what to do on the next poll: return a frame, or raise. `on_login` is what the
    real backend calls from its one authentication point; here a re-login calls it when it
    `relogin_authenticates` (False = a token refresh held, or the bridge put the attempt off)."""

    def __init__(self, on_login=None):
        self.next = None
        self.on_login = on_login
        self.relogin_raises = None
        self.relogin_calls = 0
        self.relogin_authenticates = True

    def get_status(self, vehicle=None):
        if isinstance(self.next, Exception):
            raise self.next
        return self.next

    def relogin(self):
        self.relogin_calls += 1
        if self.relogin_authenticates and self.on_login:
            self.on_login(self.relogin_raises)
        if self.relogin_raises:
            raise self.relogin_raises


def make_poll(PM, tmp_path, monkeypatch):
    """`_poll_vehicle` on a fixed clock, with everything that is not the poll stubbed out.
    Returns `run(what, advance=0)`; the database, client, account and vehicle id hang off it."""
    path = str(tmp_path / "poll.db")
    db = D.Database(path)
    vid = db.ensure_vehicle(VIN, "B10")
    clock = {"t": NOW}
    monkeypatch.setattr(PM.time, "time", lambda: clock["t"])
    for name in ("_maybe_refresh_charge_schedule", "_write_comfort_state"):
        monkeypatch.setattr(PM, name, lambda *a, **k: None)
    monkeypatch.setattr(PM.energy_snapshots, "maybe_sample", lambda *a, **k: None)
    monkeypatch.setattr(PM.ready_automation, "maybe_trigger", lambda *a, **k: None)
    monkeypatch.setattr(PM, "_mqtt_tick", lambda *a, **k: None)

    class _Vehicle:
        vin, car_type, year, abilities, is_shared = VIN, "B10", 2025, None, False

    ctx = PM.VehicleContext(db, _Vehicle(), vid)
    acct = PM.AccountState()
    client = Client(on_login=lambda exc: PM._note_login(path, exc))

    def run(what, advance=0.0):
        clock["t"] += advance
        client.next = what
        PM._poll_vehicle(db, client, ctx, acct)
        return db

    run.db, run.client, run.acct, run.ctx, run.vid = db, client, acct, ctx, vid
    return run


class Stop(BaseException):
    """Ends main(): not an Exception, so nothing in the poller can absorb it."""


class RefusingClient:
    """A cloud that refuses the first `refusals` logins — or fails them with `error` — then lets
    the test stop the process, or, with `then`, lets the next login through (True =
    authenticated, False = resumed). Built by main() with the listener the real client would
    hand its backend."""

    def __init__(self, refusals, then=None, error=None, on_login=None):
        self.refusals = refusals
        self.then = then
        self.error = error or RuntimeError("Leapmotor login failed: Error occurred")
        self.on_login = on_login
        self.attempts = 0

    def login(self):
        self.attempts += 1
        if self.attempts <= self.refusals:
            self.on_login(self.error)
            raise self.error
        if self.then is None:
            raise Stop
        if self.then:
            self.on_login(None)

    @property
    def _vehicle(self):
        raise Stop                     # main() reads it right after the login: the test ends here


def make_startup(PM, tmp_path, monkeypatch):
    """`main()` up to and including the startup login, on a clock its own sleeps advance.
    Returns `run(refusals, then=None, error=None)` → (client, database, clock at the stop)."""
    path = str(tmp_path / "beat.db")
    monkeypatch.setenv("DB_PATH", path)

    def run(refusals, then=None, error=None):
        client = RefusingClient(refusals, then, error)

        def _build(**kw):
            client.on_login = kw["on_login"]
            return client
        monkeypatch.setattr(PM, "LeapmotorMateClient", _build)
        clock = {"t": NOW}
        monkeypatch.setattr(PM.time, "time", lambda: clock["t"])
        monkeypatch.setattr(PM.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
        monkeypatch.setattr(PM, "load_config", lambda db: {
            "username": "u", "password": "p", "pin": "1234",
            "cert_path": "/tmp/c.pem", "key_path": "/tmp/k.pem"})
        database = D.Database(path)
        database.set_setting("setup_complete", "1")
        database._conn.commit()
        database._conn.close()
        try:
            PM.main()
        except Stop:
            pass
        return client, D.Database(path), clock["t"]

    return run
