"""Shared window open/closed helper + the Overview/Commands count (#62, reopened by gody01).

gody01's T03 reports windows open ONLY via the position % (3727/3728/1879/1880); the open/closed
flags (1693-1696) stay 0 even when open. The Overview tile, the Commands grid, the poller and the
post-command verification all used a FLAG-ONLY read, so they showed the T03 as "closed" (and the
"Finestrini aperti N" badge as 0) while the Vehicle page — which already reads the % — showed it
open. They now share capability_profile.window_open_states, so the state AND the count agree
everywhere. The B10 (which emits no % at all) is unchanged.

These exercise the pure helper directly (no fastapi needed), plus the any()/sum() the writers apply
to derive `windows_open` / `windows_open_count`. Values are gody01's real bundle-6 capture."""
import capability_profile as cp


# All four cracked open at 20% via the position %, flags dead — gody01's real bundle-6 values.
T03_OPEN = {"1693": False, "1694": False, "1695": False, "1696": False,
            "3727": 20, "3728": 20, "1879": 20, "1880": 20}
T03_CLOSED = {"1693": False, "1694": False, "1695": False, "1696": False,
              "3727": 0, "3728": 0, "1879": 0, "1880": 0}


def _open_and_count(states):
    """Mirror exactly what save_fresh_signals + the poller store from the helper output:
    windows_open = any open, windows_open_count = how many."""
    return int(any(states)), sum(1 for s in states if s)


def test_t03_open_all_four_from_percent():
    states = cp.window_open_states(T03_OPEN, use_pct=True)
    assert states == [True, True, True, True]
    assert _open_and_count(states) == (1, 4)          # the Overview badge must read 4, like the B10


def test_t03_partial_open_counts_correctly():
    sig = dict(T03_CLOSED, **{"3727": 20, "1879": 35})   # FL + RL open
    states = cp.window_open_states(sig, use_pct=True)
    assert states == [True, False, True, False]
    assert _open_and_count(states) == (1, 2)


def test_t03_closed_reads_closed():
    states = cp.window_open_states(T03_CLOSED, use_pct=True)
    assert states == [False, False, False, False]
    assert _open_and_count(states) == (0, 0)


def test_b10_flag_driven_when_percent_absent():
    # The B10 emits the flags and NO % keys at all. Even called with use_pct=True (the live default —
    # the windows_pct gate is never marked broken in practice), the absent % can't false-positive, so
    # the flag drives it. This is why the B10 is safe without relying on the (inert) gate.
    sig = {"1693": 1, "1694": 0, "1695": 0, "1696": 1}   # FL + RR open
    states = cp.window_open_states(sig, use_pct=True)
    assert states == [True, False, False, True]
    assert _open_and_count(states) == (1, 2)


def test_percent_ignored_when_gate_closed():
    # If a car's % is ever marked untrusted (use_pct=False), garbage on the % keys must NOT
    # false-positive — with the flags at 0 every window reads CLOSED.
    sig = {"1693": 0, "1694": 0, "1695": 0, "1696": 0,
           "3727": 50, "3728": 50, "1879": 50, "1880": 50}
    states = cp.window_open_states(sig, use_pct=False)
    assert states == [False, False, False, False]
    assert _open_and_count(states) == (0, 0)


def test_absent_signals_are_unknown_not_open():
    # Car asleep / no window signals → None per window; the writers treat None as not-open (0),
    # never a false "open".
    states = cp.window_open_states({}, use_pct=True)
    assert states == [None, None, None, None]
    assert _open_and_count(states) == (0, 0)


# ── Integration: the real write paths must store open + count (what the UI reads) ─────────────
import sqlite3

import client                      # poller/client.py
import db as poller_db             # poller/db.py
import db_reader                   # web/db_reader.py


def _web_db(tmp_path, monkeypatch, vin="VIN123"):
    path = str(tmp_path / "web.db")
    poller_db.Database(path)                       # build the full schema
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, ?)", (vin,))
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _latest_windows(path):
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    row = con.execute("SELECT windows_open, windows_open_count FROM positions "
                      "ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return row["windows_open"], row["windows_open_count"]


def test_web_save_fresh_signals_t03_open_stores_count_four(tmp_path, monkeypatch):
    # The Overview tile and Commands grid read these stored values. The T03 open at 20% must store
    # windows_open=1 and windows_open_count=4 (the "Finestrini aperti 4" badge) — not flag-only 0.
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({**T03_OPEN, "1204": 50})
    assert _latest_windows(path) == (1, 4)


def test_web_save_fresh_signals_t03_closed_stores_zero(tmp_path, monkeypatch):
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({**T03_CLOSED, "1204": 50})
    assert _latest_windows(path) == (0, 0)


def test_poller_parse_signal_t03_open():
    # The poller re-writes the same stored status every ~30s; _parse_signal must report the T03
    # windows open (the per-window fields drive windows_open_count in db.save_position).
    data = client._parse_signal("T03VIN", dict(T03_OPEN))
    assert data.windows_open is True
    assert [data.window_fl_open, data.window_fr_open,
            data.window_rl_open, data.window_rr_open] == [True, True, True, True]


def test_poller_parse_signal_b10_flag_only_no_regression():
    # B10: flags drive it, no % emitted → unchanged behaviour.
    data = client._parse_signal("B10VIN", {"1693": 1, "1694": 0, "1695": 0, "1696": 0})
    assert data.windows_open is True
    assert [data.window_fl_open, data.window_fr_open,
            data.window_rl_open, data.window_rr_open] == [True, False, False, False]


# ── #68 revisited — on the B10 the window status flag = 2 means OPEN (verified on-car) ────────────
# Ground truth from Silvio's B10, cross-checked against the official app: the flag (1693-1696) is 2
# when the window is OPEN and 0 when shut. The B10's position % (3727...) is DEAD (always 0). The
# earlier #68 read treated 2 as "closed" — that came from a stale cloud frame, not the firmware — so
# once the optimistic overlay expired Mate showed genuinely-open windows as shut. A non-zero flag is
# OPEN, and on the B10 it must win over a dead %=0.
B10_FLAG2_OPEN = {"1693": 2, "1694": 0, "1695": 0, "1696": 0,
                  "3727": 0, "3728": 0, "1879": 0, "1880": 0}


def test_b10_flag_2_reads_open():
    # The B10's % is consulted (untested → shown) but dead (0); the flag (2 = open) must win.
    states = cp.window_open_states(B10_FLAG2_OPEN, use_pct=True)
    assert states == [True, False, False, False]
    assert _open_and_count(states) == (1, 1)


def test_poller_parse_signal_b10_flag_2_open():
    # Same frame through the poller — this is what paints the live Overview car image
    # (window_fl_open / window_rl_open). A flag of 2 = open → the FL glass is drawn down.
    data = client._parse_signal("B10VIN", dict(B10_FLAG2_OPEN))
    assert data.windows_open is True
    assert [data.window_fl_open, data.window_fr_open,
            data.window_rl_open, data.window_rr_open] == [True, False, False, False]


def test_web_save_fresh_signals_b10_flag_2_stores_open(tmp_path, monkeypatch):
    # The Overview tile + "Finestrini aperti N" badge read these stored values; flag 2 = open → (1, 1).
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({**B10_FLAG2_OPEN, "1204": 50})
    assert _latest_windows(path) == (1, 1)
