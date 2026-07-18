"""get_v2l_sessions: V2L (vehicle-to-load) discharge sessions reconstructed ON-READ from the
per-poll positions log (ac_port_mode==2 + signed battery current/voltage). Power is NET of the
idle baseline captured just before each session. Pure db_reader (no fastapi) → runs in CI."""
import sqlite3
from datetime import timezone

import db_reader

BIG = 100000  # lookback_days huge so the recency cutoff never filters the test rows


def _setup(monkeypatch, rows):
    monkeypatch.setattr(db_reader, "_local_tz", lambda: timezone.utc)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE positions (recorded_at TEXT, soc REAL, charge_current_a REAL, "
                "charge_voltage_v REAL, ac_port_mode INT)")
    con.executemany("INSERT INTO positions VALUES (?,?,?,?,?)", rows)
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)


def P(mm, soc, i, v=400.0, acmode=0):
    # mm = minute within a fixed hour; i = signed battery current (A, +=discharge)
    return (f"2026-06-08T13:{mm:02d}:00+00:00", soc, i, v, acmode)


def test_session_reconstructed_with_net_power(monkeypatch):
    # idle baseline 0.5 A; then V2L at 3.0 A / 400 V → gross 1200 W, NET (3.0-0.5)*400 = 1000 W.
    _setup(monkeypatch, [
        P(0, 80, 0.5, 400, 0),                       # idle → baseline I0 = 0.5 A
        P(1, 80, 3.0, 400, 2),                        # V2L samples (01,02,03 min)
        P(2, 79, 3.0, 400, 2),
        P(3, 79, 3.0, 400, 2),
        P(4, 79, 0.5, 400, 0),                        # V2L ends
    ])
    out = db_reader.get_v2l_sessions(lookback_days=BIG)
    assert out["count"] == 1
    s = out["sessions"][0]
    assert s["peak_w"] == 1000                        # NET, not the 1200 gross
    assert s["baseline_w"] == 200                     # 0.5 A * 400 V (the car's own overhead)
    assert s["energy_wh"] == 33.3                     # 1000 W over two 1-min gaps = 2000/60 Wh
    assert s["ongoing"] is False


def test_charging_and_idle_make_no_session(monkeypatch):
    # ac_port_mode 0 (idle) and 1 (AC charging, negative current) must never open a V2L session.
    _setup(monkeypatch, [
        P(0, 80, 0.5, 400, 0), P(1, 80, -8.0, 400, 1), P(2, 82, -8.0, 400, 1), P(3, 84, 0.5, 400, 0),
    ])
    out = db_reader.get_v2l_sessions(lookback_days=BIG)
    assert out["count"] == 0 and out["total_energy_wh"] == 0.0


def test_net_power_clamped_at_zero(monkeypatch):
    # A V2L sample whose current sits AT/below the baseline (mode armed, no real draw) → 0 net power,
    # so a latched 47==2 with the load off can never inflate energy.
    _setup(monkeypatch, [
        P(0, 80, 0.7, 400, 0),                        # baseline 0.7 A
        P(1, 80, 0.7, 400, 2), P(2, 80, 0.6, 400, 2), P(3, 80, 0.7, 400, 2),   # armed, no load
    ])
    out = db_reader.get_v2l_sessions(lookback_days=BIG)
    s = out["sessions"][0]
    assert s["peak_w"] == 0 and s["energy_wh"] == 0.0
    assert s["ongoing"] is True                       # trailing session still open


def test_null_ac_port_mode_rows_dont_fragment_or_corrupt(monkeypatch):
    # Web-side live writes (save_fresh_signals) can leave ac_port_mode NULL. Interspersed among V2L
    # (mode 2) samples they must NOT split the session into fragments, and must NOT corrupt the idle
    # baseline — a NULL row carrying a high current would otherwise raise I0 and zero the net power.
    _setup(monkeypatch, [
        P(0, 80, 0.5, 400, 0),                          # idle baseline 0.5 A
        P(1, 80, 3.0, 400, 2),                          # V2L, net (3.0-0.5)*400 = 1000 W
        P(2, 80, 2.6, 400, None),                       # NULL row w/ high current — must be ignored
        P(3, 79, 3.0, 400, 2),
        P(4, 79, 0.5, 400, 0),                          # V2L ends
    ])
    out = db_reader.get_v2l_sessions(lookback_days=BIG)
    assert out["count"] == 1                            # ONE session, NOT fragmented by the NULL row
    assert out["sessions"][0]["peak_w"] == 1000         # baseline stayed 0.5 A (NULL's 2.6 A ignored)


def test_two_separate_sessions(monkeypatch):
    _setup(monkeypatch, [
        P(0, 80, 0.5, 400, 0),
        P(1, 80, 2.0, 400, 2), P(2, 79, 2.0, 400, 2),       # session 1: net (2.0-0.5)*400 = 600 W
        P(3, 79, 0.5, 400, 0),                              # gap
        P(5, 79, 5.5, 400, 2), P(6, 78, 5.5, 400, 2),       # session 2: net (5.5-0.5)*400 = 2000 W
        P(7, 78, 0.5, 400, 0),
    ])
    out = db_reader.get_v2l_sessions(lookback_days=BIG)
    assert out["count"] == 2
    assert sorted(s["peak_w"] for s in out["sessions"]) == [600, 2000]


# ── get_v2l_status (Overview card summary) ───────────────────────────────────────

def test_status_active_reports_instant_power(monkeypatch):
    _setup(monkeypatch, [
        P(0, 80, 0.5, 400, 0),                              # baseline 0.5 A
        P(1, 80, 3.0, 400, 2), P(2, 79, 3.0, 400, 2),       # ongoing V2L, net 1000 W
    ])
    st = db_reader.get_v2l_status(lookback_days=BIG)
    assert st["has_data"] and st["active"] is True
    assert st["power_w"] == 1000 and st["power_max_w"] == 3500


def test_status_idle_keeps_last_session(monkeypatch):
    _setup(monkeypatch, [
        P(0, 80, 0.5, 400, 0),
        P(1, 80, 3.0, 400, 2), P(2, 79, 3.0, 400, 2),
        P(3, 79, 0.5, 400, 0),                              # V2L ended
    ])
    st = db_reader.get_v2l_status(lookback_days=BIG)
    assert st["has_data"] and st["active"] is False
    assert st["power_w"] == 0 and st["energy_wh"] > 0


def test_status_always_shown_even_when_never_used(monkeypatch):
    # The block is ALWAYS shown — never hidden on a model guess (we don't assume which cars lack V2L).
    # Never-used → idle state (has_data True, ever_used False).
    _setup(monkeypatch, [P(0, 80, 0.5, 400, 0), P(1, 80, -8.0, 400, 1)])   # idle + charging, no V2L
    st = db_reader.get_v2l_status(lookback_days=BIG)
    assert st["has_data"] is True and st["ever_used"] is False and st["active"] is False
