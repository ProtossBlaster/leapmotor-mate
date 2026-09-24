"""Session recovery retried every 60 seconds for ever, however long the cloud kept refusing.

Measured on three installs whose bundles reach 17–18/09/2026 (beta #49 + #295 @gm27271,
#296 @adoewa, and a beta bundle from @ebagnoli). All three break the same way, line for line:

    04:34:41 [ERROR]   Poll error: ... Read timed out
    04:34:41 [INFO]    Attempting session recovery (re-login)…
    04:34:42 [WARNING] Re-login failed, will retry next cycle: Leapmotor login failed: Error occurred

The cloud drops a poll, Mate asks for a new token — the right move — and the cloud refuses. It is
not a blocked account: logins keep being accepted now and then throughout (@adoewa got one at
16:52:33 on 17/09, three minutes after three refusals). What the account lost is the rate: the
cloud took ~310 logins a day from @gm27271 until 16/09 and 5–12 a day from 17/09.

Against that, a fixed 60-second retry asks 1 440 times a day. gm made 5 271 failed attempts in
four days for ~79 accepted; adoewa 4 204 for 14. We cannot make the cloud answer, but we can stop
hammering the one thing that is rate-limiting us, and stop burying the user's log.

So the gap grows while the refusals continue — 60 s, then 120, 240, 480, 960, capped at half an
hour — and goes back to 60 the moment a login succeeds. The FIRST retry stays at 60 seconds: this
path also heals the ordinary cases (a vanished /tmp cert, one dropped token), which are fixed by
the first or second attempt, and those installs must behave exactly as they do today.
"""
import importlib.util
import pathlib
import sys

import db as D
import pytest


def _poller_main():
    """poller/main.py under its own name — a bare `import main` gets web/main.py."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_backoff", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["poller_main_backoff"] = mod
    spec.loader.exec_module(mod)
    return mod


PM = _poller_main()

DAY_S = 24 * 60 * 60


# ── the account decides how long to wait ──────────────────────────────────────

def test_the_first_attempt_waits_the_same_sixty_seconds_as_today():
    """No change for the install this self-heal was written for: a cert vanishes, one retry fixes
    it. Only a cloud that keeps refusing ever sees a longer gap."""
    acct = PM.AccountState()
    assert acct.relogin_wait_s == 60
    acct.relogin_failures = 1
    assert acct.relogin_wait_s == 60, "the gap after the first failure is today's gap"


def test_the_gap_doubles_while_the_refusals_continue():
    acct = PM.AccountState()
    seen = []
    for _ in range(6):
        seen.append(acct.relogin_wait_s)
        acct.relogin_failures += 1
    assert seen == [60, 60, 120, 240, 480, 960]


def test_the_gap_stops_growing_at_half_an_hour():
    """An outage lasting days must not push the next attempt past the point of usefulness."""
    acct = PM.AccountState()
    acct.relogin_failures = 40
    assert acct.relogin_wait_s == 1800


def test_a_login_that_succeeds_puts_the_gap_back_to_sixty(poll_once):
    """The cloud lets one through. The next drop must be met with the fast retry again, not with
    the half-hour the outage had earned."""
    acct, client = poll_once(refusals=6)
    assert acct.relogin_wait_s > 60
    client.relogin_raises = None
    poll_once.run(acct, client, advance=DAY_S)
    assert acct.relogin_failures == 0
    assert acct.relogin_wait_s == 60


# ── and the poll actually obeys it ────────────────────────────────────────────

def test_the_poll_does_not_retry_before_the_gap_has_passed(poll_once):
    """The arithmetic is worth nothing if the gate still reads a hard-coded minute."""
    acct, client = poll_once(refusals=3)          # gap is now 240 s
    before = client.relogin_calls
    poll_once.run(acct, client, advance=120)
    assert client.relogin_calls == before, "120 s is not yet 240 s"
    poll_once.run(acct, client, advance=130)
    assert client.relogin_calls == before + 1, "past 240 s it tries again"


def test_four_days_of_refusal_cost_attempts_in_the_hundreds_not_thousands(poll_once):
    """@gm27271's four days: 5 271 failed attempts. Polling every 60 s throughout, the same four
    days must now cost a couple of hundred."""
    acct, client = poll_once(refusals=0)
    for _ in range(4 * DAY_S // 60):
        poll_once.run(acct, client, advance=60)
    assert client.relogin_calls < 250, f"{client.relogin_calls} attempts in four days"
    assert client.relogin_calls > 100, "and it must still be trying, not given up"


def test_the_warning_says_when_the_next_attempt_is(poll_once, caplog):
    """The user reads this line while the car shows offline; "next cycle" stopped being true."""
    with caplog.at_level("WARNING", logger="leapmotor_mate"):
        poll_once(refusals=1)
    assert "Re-login failed" in caplog.text
    assert "60s" in caplog.text


# ── harness ───────────────────────────────────────────────────────────────────

class _Client:
    """A cloud that drops every poll and refuses every login — the measured outage."""

    def __init__(self):
        self.relogin_calls = 0
        self.relogin_raises = RuntimeError("Leapmotor login failed: Error occurred")

    def get_status(self, vehicle=None):
        raise ConnectionError("('Connection aborted.', RemoteDisconnected(...))")

    def relogin(self):
        self.relogin_calls += 1
        if self.relogin_raises:
            raise self.relogin_raises


@pytest.fixture()
def poll_once(tmp_path, monkeypatch):
    """`_poll_vehicle` against that cloud, on a clock the test moves itself."""
    path = str(tmp_path / "backoff.db")
    db = D.Database(path)
    vid = db.ensure_vehicle("VINBACKOFF0000001", "C10")
    clock = {"t": 1_760_000_000.0}
    monkeypatch.setattr(PM.time, "time", lambda: clock["t"])

    class _Vehicle:
        vin, car_type, year, abilities, is_shared = "VINBACKOFF0000001", "C10", 2025, None, False

    ctx = PM.VehicleContext(db, _Vehicle(), vid)

    def run(acct, client, advance=0):
        clock["t"] += advance
        PM._poll_vehicle(db, client, ctx, acct)
        return acct, client

    def start(refusals=0):
        acct, client = PM.AccountState(), _Client()
        for _ in range(refusals):
            run(acct, client, advance=DAY_S)     # far enough apart that the gate never holds
        return acct, client

    start.run = run
    return start
