"""A T03 reads its charge target from its charge plan (#436, @sarifarm).

A T03's status carries no charge limit: `config['3']` is empty. Its charge plan does. getAppointment
190 answered `chargesoc` on 21 reads out of 21 between 10 and 26/10/2026, and after a write it read
back what Mate had written (80 at 09:55 → 80 at 11:14, 100 at 16:22 → 100 the next morning). The
poller already read that plan every 30 minutes and dropped the target; the Charges card read only the
status. So the card showed "—" right after a save, and MQTT `charge_limit` stayed unknown.

A car whose status carries the limit keeps reading it there: on our B10 the plan is a copy of the last
command and can disagree with the car (04/10/2026).
"""
import dataclasses

import command_client
from poll_cycle_fixture import frame, make_poll, poller_main

PM = poller_main("poller_main_t03_charge_target")


# ── the Charges card: /api/charge-plan ─────────────────────────────────────────────────────────


@dataclasses.dataclass
class _Vehicle:
    vin: str = "VIN-T03"
    car_type: str = "T03"


class _Api:
    def __init__(self, config, plan):
        self.config, self.plan, self.plans_asked = config, plan, 0

    def get_vehicle_raw_status(self, vehicle):
        return {"data": {"signal": {"1204": 55}, "config": self.config}}

    def get_charge_schedule(self, vin):
        self.plans_asked += 1
        return self.plan


def _session(config, plan):
    session = command_client.LeapmotorSession()
    session._api = _Api(config, plan)
    session._vehicle = _Vehicle()
    session._vehicles = [session._vehicle]
    session._connect = lambda: None
    session._reset = lambda: None
    return session


T03_PLAN = {"chargeEnable": 0, "chargesoc": 80, "starttime": "08:18", "endtime": "06:00",
            "cycles": "1,1,1,1,1,1,1", "circulation": 0, "recharge": 0}


def test_the_card_reads_a_t03s_target_from_its_charge_plan():
    session = _session({}, T03_PLAN)
    assert session.get_charge_plan()["charge_limit_percent"] == 80


def test_a_car_whose_status_carries_the_limit_reads_it_there_and_asks_nothing_more():
    session = _session({"3": {"percent": 90, "isEnable": 1, "beginTime": "23:00", "endTime": "07:00"}},
                       dict(T03_PLAN, chargesoc=70))
    assert session.get_charge_plan()["charge_limit_percent"] == 90
    assert session._api.plans_asked == 0


def test_a_plan_that_cannot_be_read_leaves_the_target_unknown():
    session = _session({}, None)
    assert session.get_charge_plan()["charge_limit_percent"] is None


# ── the poller: MQTT charge_limit and the Overview's "to X %" ──────────────────────────────────


def _poll(tmp_path, monkeypatch, status_limit, plan):
    PM._plan_target.clear()
    run = make_poll(PM, tmp_path, monkeypatch)
    published = []
    monkeypatch.setattr(PM, "_mqtt_tick", lambda db, client, data, *a, **k: published.append(data.charge_limit_percent))
    monkeypatch.setattr(PM, "_maybe_refresh_charge_schedule",
                        lambda db, client: PM._store_charge_schedule(db, plan, run.ctx.vin))
    f = frame(int(PM.time.time() * 1000), vin=run.ctx.vin)
    f.charge_limit_percent = status_limit
    run(f)
    return published, run.db.get_own_charge_limit_percent(run.ctx.vin)


def test_the_poller_publishes_a_t03s_target_from_its_charge_plan(tmp_path, monkeypatch):
    published, stored = _poll(tmp_path, monkeypatch, None, T03_PLAN)
    assert published == [80]
    assert stored == "80"


def test_the_poller_keeps_the_limit_a_status_carries(tmp_path, monkeypatch):
    published, stored = _poll(tmp_path, monkeypatch, 90, dict(T03_PLAN, chargesoc=70))
    assert published == [90]
    assert stored == "90"


def test_a_plan_without_a_target_leaves_it_unknown(tmp_path, monkeypatch):
    published, stored = _poll(tmp_path, monkeypatch, None, {k: v for k, v in T03_PLAN.items() if k != "chargesoc"})
    assert published == [None]
    assert not stored


# ── a target Mate writes is the plan's target at once, as the window already is (#173) ─────────


def _command(tmp_path, cmd, value):
    import types
    import db as D
    PM._plan_target.clear()
    api = types.SimpleNamespace(get_charge_schedule=lambda vin: dict(T03_PLAN),
                                set_charge_schedule=lambda vin, **kw: None)
    service = types.SimpleNamespace(last_climate_on=None, publish_state=lambda vin, k, v: None)
    PM._handle_mqtt_command(types.SimpleNamespace(_api=api), service,
                            D.Database(str(tmp_path / "t.db")), "VIN1", cmd, value)
    return PM._plan_target.get("VIN1")


def test_a_target_set_from_home_assistant_is_the_plans_target_at_once(tmp_path):
    assert _command(tmp_path, "charge_limit", "92") == 92


def test_a_plan_set_from_home_assistant_carries_its_target_at_once(tmp_path):
    assert _command(tmp_path, "charge_schedule", '{"soc": 77}') == 77
