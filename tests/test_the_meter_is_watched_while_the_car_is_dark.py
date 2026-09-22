"""The home wallbox counter must keep being read while the CAR's cloud is dark (#295 @gm27271).

His C10 charged from 19:37 to 07:32. Mate opened the charge at 20:35, lost the Leapmotor cloud at
20:38, and got it back for nine minutes at 22:04. The meter is in his house and never went away —
but Mate only read it inside `state == CHARGING`, and a poll that raises never reaches that branch,
so for 86 minutes nobody looked at a counter sitting in the same room. Everything it did in the
dark arrived as ONE step, where a reset is indistinguishable from a rise and the #46 ceiling (22 kW
× hours × 1.5) is far too wide to object. The charge came out at 7.69 kWh AC against 10.03 kWh DC:
a 130.4 % "efficiency", which the wallbox page then printed in green.

The fix is where it belongs: the meter is read because a CHARGE IS OPEN, not because a remote cloud
answered this minute. The loop already keeps polling every 60 s through an outage — this rides on
that, and the charge survives the gap by design (see the resume comment in recorder.py).
"""
import dataclasses
import importlib.util
import pathlib
import sys

import db as D
import pytest
from recorder import Recorder


def _poller_main():
    """poller/main.py under its own name — a bare `import main` gets web/main.py."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_dark_meter", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["poller_main_dark_meter"] = mod
    spec.loader.exec_module(mod)
    return mod


PM = _poller_main()

_DEFAULTS = {"latitude": 45.0, "longitude": 9.0, "range_km": 300, "charging_status": 0,
             "is_locked": True, "security_active": True, "charge_limit_percent": 80,
             "raw_signals": {}, "is_reev": False,
             # Both are read as strings on the charging path (`.lower()`), and a None there
             # surfaces as a poll error that looks like the cloud's fault, not the test's.
             "vin": "VINDARK00000000001", "vehicle_state": "parked"}


def _frame(soc=42.1, charging=False, ts=1_760_000_000_000):
    """One cloud frame, built field by field from the REAL dataclass so it can never drift."""
    import client as _client
    kw = {}
    for f in dataclasses.fields(_client.VehicleData):
        if (f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING):
            continue
        kw[f.name] = _DEFAULTS.get(f.name, 0.0 if f.type in ("float", float) else None)
    kw.update(soc=soc, odometer_km=9258.0, gear="P", speed_kmh=0.0, timestamp_ms=ts,
              charging_status=1 if charging else 0, plug_connected=charging,
              charge_power_kw=6.1 if charging else 0.0)
    return _client.VehicleData(**kw)


class _Stop(BaseException):
    """Ends the loop without anything inside it being able to absorb it."""


class _Vehicle:
    def __init__(self):
        self.vin, self.car_type, self.year, self.abilities = "VINDARK00000000001", "C10", 2025, None
        self.is_shared = False


class _Client:
    """A cloud that hands back canned frames, then refuses — like his did at 20:38."""

    def __init__(self, frames):
        self._vehicle = _Vehicle()
        self._vehicles = [self._vehicle]
        self._frames = list(frames)
        self.calls = 0
        self.budget = 10 ** 6

    def login(self):
        pass

    def relogin(self):
        pass

    def get_status(self, vehicle=None):
        if self.calls >= self.budget:
            raise _Stop
        self.calls += 1
        f = self._frames.pop(0) if self._frames else _frame()
        if isinstance(f, Exception):
            raise f
        return f

    def close(self):
        pass


@pytest.fixture
def loop(tmp_path, monkeypatch):
    """`main()` against a stub cloud and a real database, with the meter under the test's control.

    Returns (db, readings_taken): the list is appended to on every call the poller makes to the
    wallbox, so a test can assert not just the total but WHEN Mate bothered to look.
    """
    path = str(tmp_path / "dark.db")
    monkeypatch.setenv("DB_PATH", path)

    def run(frames, meter, rounds=4):
        taken: list = []
        queue = list(meter)

        def _read(self):
            v = queue.pop(0) if queue else (taken[-1] if taken else None)
            taken.append(v)
            return v

        monkeypatch.setattr(Recorder, "_read_wallbox_energy", _read)
        client = _Client(frames)
        client.budget = rounds
        monkeypatch.setattr(PM, "LeapmotorMateClient", lambda **kw: client)

        clock = {"t": 1_760_000_000.0}
        monkeypatch.setattr(PM.time, "time", lambda: clock["t"])
        monkeypatch.setattr(PM.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
        for name in ("_maybe_check_ota", "_maybe_refresh_charge_schedule"):
            monkeypatch.setattr(PM, name, lambda *a, **k: None)
        monkeypatch.setattr(PM.energy_snapshots, "maybe_sample", lambda *a, **k: None)
        monkeypatch.setattr(PM.ready_automation, "maybe_trigger", lambda *a, **k: None)
        monkeypatch.setattr(PM, "_mqtt_tick", lambda *a, **k: None)
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
        return D.Database(path), taken

    return run


def _charge(db):
    return db._conn.execute(
        "SELECT ac_energy_kwh, wallbox_energy_start_kwh, wb_stuck_kwh FROM charges "
        "ORDER BY id DESC LIMIT 1").fetchone()


# ── the counter is read because a charge is open, not because the cloud answered ──

def test_the_counter_is_read_on_a_poll_the_cloud_refused(loop):
    """His outage, in four polls: one that opens the charge, then three the cloud refuses.
    The meter is in the house — it must be read on all four."""
    db, taken = loop(
        frames=[_frame(charging=True), RuntimeError("Token is invalid"),
                RuntimeError("Leapmotor login failed"), RuntimeError("Leapmotor login failed")],
        meter=[11.78, 12.50, 13.20, 13.90], rounds=4)
    assert len(taken) >= 4, f"the meter was read on {len(taken)} polls out of 4"
    assert _charge(db)["ac_energy_kwh"] == pytest.approx(2.12, abs=0.01)


def test_a_reset_in_the_dark_is_seen_as_a_reset(loop):
    """Read every poll, a counter that zeroes is a single negative step: ignored, baseline lowered,
    the rises after it still counted. Seen only once, at the end, the same zeroing is invisible."""
    db, _ = loop(
        frames=[_frame(charging=True), RuntimeError("down"), RuntimeError("down"),
                RuntimeError("down")],
        meter=[11.78, 0.40, 1.40, 2.10], rounds=4)
    assert _charge(db)["ac_energy_kwh"] == pytest.approx(1.70, abs=0.01)


def test_the_dark_polls_never_invent_energy_the_car_never_reported(loop):
    """With the cloud down we do not know what the car was drawing, so a flat counter proves
    nothing: wb_stuck_kwh must not grow, or the #215 backstop would throw away a good meter."""
    db, _ = loop(
        frames=[_frame(charging=True), RuntimeError("down"), RuntimeError("down"),
                RuntimeError("down")],
        meter=[11.78, 11.78, 11.78, 11.78], rounds=4)
    # The ONE charging poll legitimately accrues (the car reported 6.1 kW and the counter
    # did not move): 6.1 kW × 30 s = 0.051 kWh. The three dark polls must add nothing to it.
    assert (_charge(db)["wb_stuck_kwh"] or 0.0) == pytest.approx(0.051, abs=0.005)


def test_no_charge_open_means_no_wallbox_call(loop):
    """A parked car with the cloud down must not poll Home Assistant on every cycle."""
    _, taken = loop(frames=[_frame(), RuntimeError("down"), RuntimeError("down")],
                    meter=[], rounds=3)
    assert taken == []


def test_a_charge_away_from_the_wallbox_is_still_not_attributed(loop, monkeypatch):
    """The home-radius rule (#46/#215 family) decides attribution; reading in the dark must not
    quietly bypass it and hang the house meter on a public charge."""
    monkeypatch.setattr(D.Database, "wallbox_energy_applies", lambda *a, **k: False)
    db, taken = loop(
        frames=[_frame(charging=True), RuntimeError("down"), RuntimeError("down")],
        meter=[11.78, 12.50, 13.20], rounds=3)
    assert taken == []
    assert _charge(db)["ac_energy_kwh"] is None
