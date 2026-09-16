"""Every charge Mate writes says WHY it stopped — #289, and Silvio's call on 16/09/2026.

WHY. Reading a duration cannot tell you whether a charge ended because the cable came out or
because the car fell asleep and Mate gave up on it. That is exactly the question #289 cost half a
day of log archaeology to answer, and it is not a rare corner: replaying @juan-conca's own twelve
days through the fixed recorder, **9 of his 13 charges** close because the car went quiet, not
because anything was unplugged. Without the reason written down, the row that says "239 min" and
the row that says "239 min" are indistinguishable, and only one of them was watched to the end.

WHAT IS RECORDED. One word per charge, in `charges.close_reason`:

    unplugged      the cable read gone — the ordinary end of a charge
    deferred       cable still in, the car postponed the charge to its programmed window (1149==4)
    car_quiet      nothing but the cable held it open, on a frame the cloud repeated for 30 min
    drove_away     the car reappeared on the road with the charge still open (#208)
    outage         the cloud refused us, and the cable was gone when it came back (30/08 audit)
    reconstructed  never seen live: built from a SoC that rose while the car was dark

🔑 THE INVARIANT, and the reason it is worth a test of its own: **every charge Mate writes carries
one**. So `NULL` means exactly one thing — the row predates this version — and never "we forgot to
say". A new close path added later without a reason breaks the last test in this file.

⚠️ Diagnostic, not a screen. It goes in the bundle beside `recon=`, where triage reads it. Nothing
in the UI shows it, and nothing prices or counts on it.
"""
from datetime import datetime, timezone

import pytest
import state_machine as SM
from state_machine import State
from client import VehicleData
import db as D
import db_reader
import diagnostics
import recorder as R


# ── helpers ───────────────────────────────────────────────────────────────────
def _vd(soc=80.0, *, gear="P", speed=0.0, charging=0, plug=True, deferred=False,
        odo=1000.0, ts_ms=0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts_ms, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="parked",
        charging_status=charging, charge_power_kw=3.5, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=plug, remaining_charge_min=0,
        charge_voltage_v=230.0, charge_current_a=-15.0, charge_deferred=deferred,
    )


def _ms(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


class _Clock:
    """Mate's two clocks move together: the one that dates rows and the one the state machine
    measures elapsed time with. Driving only the first leaves the frozen guard reading milliseconds."""

    def __init__(self, monkeypatch, start="2026-09-15T12:00:00+00:00"):
        self.now = start
        monkeypatch.setattr(D, "_now_iso", lambda: self.now)
        monkeypatch.setattr(R, "_now_iso", lambda: self.now)
        monkeypatch.setattr(SM.time, "monotonic", lambda: datetime.fromisoformat(self.now).timestamp())

    def poll(self, rec, data):
        self.now = datetime.fromtimestamp(data.timestamp_ms / 1000, timezone.utc).isoformat()
        rec.process(data)

    def resend(self, rec, data, iso):
        self.now = iso
        rec.process(data)


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    db = D.Database(path)
    db.set_battery_capacity(69.9)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setenv("DB_PATH", path)
    vid = db.ensure_vehicle("TESTVIN", "C10")
    return db, R.Recorder(db, vehicle_id=vid), _Clock(monkeypatch), vid


def _reasons(db):
    return [r["close_reason"] for r in
            db._conn.execute("SELECT close_reason FROM charges ORDER BY id").fetchall()]


def _charging(rec, clock, at="2026-09-15T12:00:00+00:00", soc=78.5):
    clock.poll(rec, _vd(soc, charging=2, ts_ms=_ms(at)))
    assert rec.state == State.CHARGING


# ── one word per way a charge can end ─────────────────────────────────────────

def test_the_cable_coming_out_says_unplugged(car):
    db, rec, clock, _ = car
    _charging(rec, clock)
    clock.poll(rec, _vd(89.0, plug=False, ts_ms=_ms("2026-09-15T16:00:00+00:00")))
    assert _reasons(db) == ["unplugged"]


def test_a_charge_postponed_to_its_window_says_deferred(car):
    """1149 == 4 (#243): cable in, the car deliberately not drawing."""
    db, rec, clock, _ = car
    _charging(rec, clock)
    clock.poll(rec, _vd(89.0, plug=True, deferred=True, ts_ms=_ms("2026-09-15T16:00:00+00:00")))
    assert _reasons(db) == ["deferred"]


def test_a_charge_given_up_on_because_the_car_went_quiet_says_car_quiet(car):
    """@juan-conca's night: current stops, the cable still reads connected, and the cloud repeats
    that one frame until morning."""
    db, rec, clock, _ = car
    _charging(rec, clock)
    stopped = _vd(89.0, plug=True, ts_ms=_ms("2026-09-15T16:00:30+00:00"))
    clock.poll(rec, stopped)
    for minute in range(1, 40):
        clock.resend(rec, stopped, "2026-09-15T16:%02d:30+00:00" % minute)
    assert _reasons(db) == ["car_quiet"]


def test_a_charge_the_car_drove_away_from_says_drove_away(car):
    """#208, @mikeeeeekoo: the car turns up on the road with the charge still open.

    🔑 It has to arrive through OFFLINE, and that is not a detail of the test: from CHARGING the
    state machine has no edge to DRIVING at all. A car that simply unplugs and leaves is closed by
    the ordinary branch on the same poll, as `unplugged` — which is what it is. `drove_away` is
    reachable only when the cloud stopped answering mid-charge and the car reappeared already
    moving, which is exactly the case #208 was written for. Learned by watching this test read
    `unplugged` when it drove off without the outage."""
    db, rec, clock, _ = car
    _charging(rec, clock)
    # …and it has to have charged something: the close dates itself from the last reading taken
    # while charging, and a car that has MOVED keeps that measured end rather than the live SoC —
    # so without this second poll the row gains no energy and is dropped as a phantom.
    clock.poll(rec, _vd(89.0, charging=2, ts_ms=_ms("2026-09-15T16:00:00+00:00")))
    for _ in range(3):
        rec.mark_offline()
    clock.poll(rec, _vd(88.0, gear="D", speed=40.0, plug=False, odo=1010.0,
                        ts_ms=_ms("2026-09-15T18:00:00+00:00")))
    assert _reasons(db) == ["drove_away"]


def test_a_charge_left_open_by_an_outage_says_outage(car):
    """The cloud refused us mid-charge, and the cable was gone when it answered again."""
    db, rec, clock, _ = car
    _charging(rec, clock)
    for _ in range(3):
        rec.mark_offline()
    assert rec.state == State.OFFLINE
    clock.poll(rec, _vd(89.0, plug=False, ts_ms=_ms("2026-09-15T18:00:00+00:00")))
    assert _reasons(db) == ["outage"]


def test_a_charge_rebuilt_from_a_soc_rise_says_reconstructed(car):
    """Never seen live: no plug, no current, only a battery that is fuller than it was."""
    db, _, _, vid = car
    db.create_reconstructed_charge(vid, 40.0, "2026-09-15T02:00:00+00:00",
                                   _vd(80.0, plug=False, ts_ms=_ms("2026-09-15T08:00:00+00:00")))
    assert _reasons(db) == ["reconstructed"]


# ── the invariant ─────────────────────────────────────────────────────────────

def test_every_charge_mate_writes_carries_a_reason(car):
    """So NULL means "written before this version", and never "we forgot to say".

    A close path added later without a reason lands here rather than on a user's bundle."""
    db, rec, clock, vid = car
    _charging(rec, clock)
    clock.poll(rec, _vd(89.0, plug=False, ts_ms=_ms("2026-09-15T16:00:00+00:00")))
    _charging(rec, clock, at="2026-09-16T12:00:00+00:00", soc=60.0)
    clock.poll(rec, _vd(70.0, plug=True, deferred=True, ts_ms=_ms("2026-09-16T16:00:00+00:00")))
    db.create_reconstructed_charge(vid, 40.0, "2026-09-17T02:00:00+00:00",
                                   _vd(80.0, plug=False, ts_ms=_ms("2026-09-17T08:00:00+00:00")))

    reasons = _reasons(db)
    assert len(reasons) == 3, reasons
    assert all(r for r in reasons), f"a charge was written without a reason: {reasons}"


# ── and triage can read it ────────────────────────────────────────────────────

def test_the_bundle_prints_the_reason_beside_recon(car):
    db, rec, clock, _ = car
    _charging(rec, clock)
    stopped = _vd(89.0, plug=True, ts_ms=_ms("2026-09-15T16:00:30+00:00"))
    clock.poll(rec, stopped)
    for minute in range(1, 40):
        clock.resend(rec, stopped, "2026-09-15T16:%02d:30+00:00" % minute)

    body = diagnostics.build_bundle("9.9.9")
    section = body.split("----- charges (from the database)", 1)[1].split("\n-----", 1)[0]
    assert "close=car_quiet" in section, section
