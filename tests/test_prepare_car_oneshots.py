"""#116 — a SPENT one-shot prepare-car appointment (no weekday + a start_time already in the past)
must be dropped from the full-state cmd-361 write, or the cloud -2-rejects the WHOLE batch and NO
schedule can be saved. Isolated on-car: recurring+past and one-shot+future both save fine; ONLY the
one-shot+past combination fails. These stale one-shots are inherited from the official app (schedules
live on the shared cloud), so Mate must filter them itself.

Skipped where command_client cannot be imported; the drop logic is pure and
is exercised in-container / wherever the client is available."""
import pytest

cc = pytest.importorskip("command_client", reason="needs the web runtime")

PAST = "2020-01-01 12:00:00"      # unambiguously in the past, whenever the test runs
FUTURE = "2099-12-31 07:00:00"    # unambiguously in the future


def _entry(days, start):
    return {"datacontent": {"air_condition": {"mode": "hot"}}, "days": days,
            "enable": True, "set_id": f"s-{start}", "start_time": start}


def test_drops_only_spent_oneshots():
    kept = cc._drop_past_oneshots([
        _entry([], PAST),                       # spent one-shot          → DROP
        _entry([], FUTURE),                     # pending one-shot        → keep
        _entry([3], "2020-05-29 18:00:00"),     # recurring, stale anchor → keep (cloud recomputes)
        _entry([4, 6], FUTURE),                 # recurring, future       → keep
    ])
    starts = [e["start_time"] for e in kept]
    assert PAST not in starts                   # the spent one-shot is the only entry removed
    assert "2020-05-29 18:00:00" in starts      # a recurring entry is kept even with a stale anchor
    assert len(kept) == 3
    assert [e["days"] for e in kept] == [[], [3], [4, 6]]   # order + the others preserved


def test_riri19_payload_leaves_only_the_weekly():
    # riri19's exact case (#116): 4 spent one-shots + 1 real weekly recurring.
    controls = [
        _entry([], "2020-01-18 12:45:14"), _entry([], "2020-01-04 14:10:21"),
        _entry([], "2020-01-03 10:58:43"), _entry([], "2020-05-29 18:00:19"),
        _entry([4, 6], FUTURE),
    ]
    kept = cc._drop_past_oneshots(controls)
    assert len(kept) == 1
    assert kept[0]["days"] == [4, 6]


def test_empty_and_unparseable_are_safe():
    assert cc._drop_past_oneshots([]) == []
    # a garbled/foreign entry must never crash the write nor be silently dropped
    weird = [{"days": [], "start_time": "not-a-date"}, {"foo": "bar"}]
    assert cc._drop_past_oneshots(weird) == weird
