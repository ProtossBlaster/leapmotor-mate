"""The poller's and the web's `capability_profile.py` carry the same tables, or one process lies.

The two processes cannot share a module (five module names collide between `poller/` and `web/`,
see the note in web/main.py), so the capability tables live in two copies. Nothing pinned them to
each other, and they drifted: the web's COMMAND_FEATURE learned the passenger-seat commands on the
day the comfort family arrived, the poller's never did. `command_shown()` treats a command it has
no row for as always shown, so on a T03 — heated and ventilated seats known absent — Home Assistant
kept the passenger-seat buttons while the driver-seat ones were hidden, and a car whose seat
commands were classified broken kept them too.

Every data table below must be equal in both copies; a row added to one and not the other fails
here, not in an owner's dashboard.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(where):
    spec = importlib.util.spec_from_file_location(f"cp_{where}", ROOT / where / "capability_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


POLLER, WEB = _load("poller"), _load("web")
TABLES = sorted({n for n in dir(POLLER) if n.isupper()} | {n for n in dir(WEB) if n.isupper()})


def test_the_copies_define_the_same_tables():
    assert TABLES == ["CHARGE_SCHEDULE_WEEKLY_ABILITY", "COMMAND_ABILITY", "COMMAND_FEATURE",
                      "FEATURES", "MODEL_ABSENT", "_WINDOW_PAIRS"]


@pytest.mark.parametrize("table", TABLES)
def test_a_table_reads_the_same_in_both_processes(table):
    assert getattr(POLLER, table, None) == getattr(WEB, table, None), table


@pytest.mark.parametrize("key", ["seat_heat_passenger_on", "seat_heat_passenger_off",
                                 "seat_vent_passenger_on", "seat_vent_passenger_off"])
def test_the_poller_hides_a_passenger_seat_command_where_it_hides_the_drivers(key):
    driver = key.replace("passenger", "driver")
    shown = lambda k: POLLER.command_shown("VIN", k, lambda s, d="": d, car_type="T03")
    assert shown(key) is shown(driver) is False


def test_every_name_the_poller_reads_is_in_the_poller_copy():
    """The poller process imports ITS copy; the test suite imports the web one (web/ comes first on
    the path in conftest). #365's range rule was first written into the web copy alone: every test
    passed, and in the container the poller failed every poll on `module 'capability_profile' has
    no attribute 'battery_range_km'` — caught on a live installation before release."""
    import re
    used = set()
    for path in (ROOT / "poller").glob("*.py"):
        if path.name != "capability_profile.py":
            used |= set(re.findall(r"capability_profile\.([A-Za-z_]\w*)", path.read_text()))
    assert used, "the scan found nothing to check"
    assert sorted(n for n in used if not hasattr(POLLER, n)) == []


@pytest.mark.parametrize("sig", [{"1204": 100, "3260": 0}, {"1204": 100}, {"1204": 3, "3260": 0},
                                 {"100003": "97.4", "3260": 426}, {"1204": 100, "3260": ""},
                                 {"100003": "", "1204": 90, "3260": 0}])
def test_the_range_rule_reads_the_same_in_both_processes(sig):
    """One rule for the two writers of `positions.range_km` (#365): the poller stores the poll, the
    web stores a refresh."""
    assert POLLER.battery_range_km(sig) == WEB.battery_range_km(sig)
