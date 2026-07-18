"""get_vampire_drain: SoC lost while PARKED and NOT charging, from the per-poll positions log.
Windows are bounded by driving (speed OR an odometer rise) or charging; short/tiny drops are dropped.
Pure db_reader (no fastapi) → runs in CI."""
import sqlite3
from datetime import timezone

import db_reader

BIG = 100000  # lookback_days huge so the test rows are never filtered by the recency cutoff


def _setup(monkeypatch, rows):
    monkeypatch.setattr(db_reader, "_local_tz", lambda: timezone.utc)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE positions (recorded_at TEXT, soc REAL, charging INT, "
                "speed_kmh REAL, odometer_km REAL, ac_port_mode INT, ready INT)")
    con.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?,?)", rows)
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)


def P(hhmm, soc, charging=0, speed=0, odo=1000.0, acmode=0, ready=None):
    # acmode = positions.ac_port_mode: 0 idle / 1 AC charging / 2 V2L discharge
    # ready  = positions.ready: 1 ON3 (car on) / 0 off / None = pre-signal → falls back to speed<1
    return (f"2026-06-08T{hhmm}:00+00:00", soc, charging, speed, odo, acmode, ready)


def test_basic_parked_drain(monkeypatch):
    # parked & unplugged, SoC 80→77 over 6h → one window, 3% / 6h = 12 %/day
    _setup(monkeypatch, [P("00:00", 80), P("02:00", 79), P("04:00", 78), P("06:00", 77)])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    w = out["windows"][0]
    assert (w["drop_pct"], w["hours"], w["pct_per_day"]) == (3.0, 6.0, 12.0)
    assert out["typical_pct_per_day"] == 12.0


def test_driving_breaks_window_and_is_not_counted(monkeypatch):
    # park A (80→78, 4h) · a drive (speed>0, consumes 78→77) · park B (77→75, 3h)
    _setup(monkeypatch, [
        P("00:00", 80, odo=1000), P("04:00", 78, odo=1000),
        P("04:30", 77, speed=40, odo=1010),                 # driving → breaks A, not counted
        P("05:00", 77, odo=1010), P("08:00", 75, odo=1010),
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 2
    drops = sorted(w["drop_pct"] for w in out["windows"])
    assert drops == [2.0, 2.0]                              # the 1% driving loss is excluded


def test_odometer_jump_breaks_window(monkeypatch):
    # park A (80→79, 3h) · GAP with a drive (odo +50, no speed sample, 79→70) · park B (70→69, 3h)
    _setup(monkeypatch, [
        P("00:00", 80, odo=1000), P("03:00", 79, odo=1000),
        P("09:00", 70, odo=1050), P("12:00", 69, odo=1050),  # odo jumped → a drive happened → break
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 2
    # the 9% lost to the (unsampled) drive must NOT appear as a window
    assert all(w["drop_pct"] <= 1.5 for w in out["windows"])


def test_short_tiny_and_charging_are_excluded(monkeypatch):
    _setup(monkeypatch, [
        P("00:00", 80), P("00:30", 79),                     # 30 min < 1h → excluded
        P("10:00", 60, charging=1), P("12:00", 70, charging=1),  # charging → not a park
        P("20:00", 50), P("23:00", 49.9),                   # 0.1% drop < 0.2 → jitter, excluded
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 0 and out["typical_pct_per_day"] is None


def test_issue41_short_window_rate_is_flagged_unreliable(monkeypatch):
    # riri19's exact case (#41): 0.4% over ~1.1h extrapolates to ~9 %/day. The window stays
    # charted (the drop is real awake-state draw) but its rate must carry a wide error band
    # and reliable=False so the UI renders it as an estimate, not an alarm.
    _setup(monkeypatch, [P("00:00", 56.8), P("01:06", 56.4)])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    w = out["windows"][0]
    assert w["drop_pct"] == 0.4 and w["pct_per_day"] > 8
    assert w["reliable"] is False and w["rate_err"] >= 4
    assert w["ongoing"] is True                             # trailing park is still open


def test_exact_4_quanta_drop_over_long_park_is_reliable(monkeypatch):
    # 77.3−76.9 is 0.3999… in float — the rounded comparison must not coin-flip it (#41
    # verifier finding). 0.4% over 6h: relative error ≤50%, band ±0.8 %/day → reliable.
    _setup(monkeypatch, [
        P("00:00", 77.3), P("06:00", 76.9),
        P("06:30", 76.5, speed=40, odo=1010),               # drive closes the window
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    w = out["windows"][0]
    assert w["drop_pct"] == 0.4 and w["rate_err"] == 0.8
    assert w["reliable"] is True and w["ongoing"] is False


def test_raised_threshold_keeps_headline_and_reveals_below_count(monkeypatch):
    # riri19 #63: two real parked windows (0.3% and 1.4% drop). Raising the display threshold must
    # THIN the chart, not blank the page — measurable_count + typical survive a high min_drop_pct,
    # and below_threshold tells the UI how many windows are hidden so it can say "below your
    # threshold" instead of the misleading "no parked data at all".
    rows = [
        P("00:00", 80.0, odo=1000), P("02:00", 79.7, odo=1000),     # park A: 0.3% / 2h
        P("02:30", 79.7, speed=40, odo=1010),                       # drive closes A
        P("03:00", 79.7, odo=1010), P("06:00", 78.3, odo=1010),     # park B: 1.4% / 3h (ongoing)
    ]
    _setup(monkeypatch, rows)

    at02 = db_reader.get_vampire_drain(lookback_days=BIG, min_drop_pct=0.2)
    assert (at02["count"], at02["measurable_count"], at02["below_threshold"]) == (2, 2, 0)

    at05 = db_reader.get_vampire_drain(lookback_days=BIG, min_drop_pct=0.5)
    assert (at05["count"], at05["measurable_count"], at05["below_threshold"]) == (1, 2, 1)
    assert at05["typical_pct_per_day"] is not None

    # The exact #63 regression: a slider above every drop charts nothing, but the data still exists.
    at20 = db_reader.get_vampire_drain(lookback_days=BIG, min_drop_pct=2.0)
    assert (at20["count"], at20["measurable_count"], at20["below_threshold"]) == (0, 2, 2)
    assert at20["typical_pct_per_day"] is not None        # headline survives → page isn't "empty"
    assert at20["min_drop_pct"] == 2.0                    # echoed back for the bundle + UI hint


def test_typical_is_time_weighted_and_counts_zero_drop_parks(monkeypatch):
    # 6h/1.0% park + (drive) + 12h/0.0% park → the zero-drop park is not charted but still
    # weighs the headline: 1.0% / 18h × 24 = 1.3 %/day, NOT the charted window's 4.0.
    _setup(monkeypatch, [
        P("00:00", 80, odo=1000), P("06:00", 79, odo=1000),
        P("06:30", 79, speed=40, odo=1010),
        P("07:00", 79, odo=1010), P("19:00", 79, odo=1010),
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    assert out["windows"][0]["pct_per_day"] == 4.0
    assert out["typical_pct_per_day"] == 1.3


def test_v2l_discharge_is_excluded_from_drain(monkeypatch):
    # Parked + V2L active (ac_port_mode=2) powering an external load: SoC drops fast, but that's
    # bidirectional-discharge OUTPUT, not standby drain. It must be carved out of the vampire metric
    # exactly like charging — never charted, never in the headline. Without the exclusion the whole
    # 00:00→10:30 span fuses into ONE 6.4%/10.5h ≈ 14.6 %/day window (under the 15%/day active-use
    # gate, so it would NOT self-exclude) and a 1.4 kW V2L session reads as a huge "vampire drain".
    _setup(monkeypatch, [
        P("00:00", 80.0, odo=1000), P("04:00", 79.6, odo=1000),               # park A: real standby 0.4%/4h
        P("04:15", 79.0, acmode=2, odo=1000), P("05:00", 76.0, acmode=2, odo=1000),
        P("06:15", 74.0, acmode=2, odo=1000),                                 # V2L 79.6→74 — must be excluded
        P("06:30", 74.0, odo=1000), P("10:30", 73.6, odo=1000),               # park B after V2L: 0.4%/4h
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    # Only the two genuine standby parks survive; the V2L discharge window is gone.
    assert sorted(w["drop_pct"] for w in out["windows"]) == [0.4, 0.4]
    assert all(w["drop_pct"] < 1.0 for w in out["windows"])          # no V2L drop leaked into a window
    assert out["typical_pct_per_day"] is not None and out["typical_pct_per_day"] < 5


# ── Ready-based drain: power-OFF → next power-ON (precise) ─────────────────────

def test_ready_off_window_is_counted(monkeypatch):
    """New logic: a stretch with the car OFF (ready=0) draining → counted as vampire over exactly the
    Ready-OFF→Ready-ON span (3% over 6h here)."""
    _setup(monkeypatch, [P("00:00", 80, ready=0), P("02:00", 79, ready=0),
                         P("04:00", 78, ready=0), P("06:00", 77, ready=0)])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1 and out["windows"][0]["drop_pct"] == 3.0


def test_ready_on_idle_is_not_counted(monkeypatch):
    """Car stationary but ON (Ready+P, ready=1, speed 0) draining climate → NOT vampire: that's the
    driving session's on-state idle, not standby. No window."""
    _setup(monkeypatch, [P("00:00", 80, ready=1), P("02:00", 78, ready=1), P("04:00", 76, ready=1)])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 0 and out["typical_pct_per_day"] is None


def test_off_climate_high_rate_is_included_not_excluded(monkeypatch):
    """Remote pre-conditioning while OFF (ready=0, 4% over 2h ≈ 48 %/day): the OLD speed logic would
    drop it as 'active use'; the NEW logic INCLUDES it (off-state drain) and only FLAGS it amber."""
    _setup(monkeypatch, [P("00:00", 80, ready=0), P("02:00", 76, ready=0)])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    assert out["windows"][0]["drop_pct"] == 4.0 and out["windows"][0]["active_use"] is True
    assert out["typical_pct_per_day"] is not None        # counted in the headline, not excluded
