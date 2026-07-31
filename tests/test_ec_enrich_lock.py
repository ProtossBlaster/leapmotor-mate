"""Per-trip EC (getEC) enrichment lock — the part that decides when a trip's cloud energy
split is considered FINAL (ec_stable=1) and overrides the SoC efficiency.

Regression for the 28/06 field bug: trips 166/167 didn't lock on their own and had to be
locked by hand. Root fragility (reproduced below as scenario C): the cloud quantizes EC to
0.1 kWh, so a value wobbling ONE step across a rounding boundary (1.9↔2.0) never satisfied the
old 0.05-abs "two equal reads" rule → it never locked. The fix: a 0.15/5% convergence tolerance
plus an age backstop that GUARANTEES an autonomous lock once the trip is old enough.

No network, no real settings DB — a tmp_path poller DB with db_reader pointed at it, and the
cloud call stubbed to a scripted sequence of reads.
"""
from datetime import datetime, timedelta, timezone

import pytest

import db as D            # poller schema (trips/settings + migrations)
import db_reader
import ec_enrich
import command_client


def _setup(tmp_path, monkeypatch, *, age_min):
    """One finalized trip ended `age_min` ago, feature enabled, cutoff before it."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    ended = now - timedelta(minutes=age_min)
    started = ended - timedelta(minutes=5)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " efficiency_kwh_100km) VALUES (1, 1, ?, ?, 7.0, 30.0)",
        (started.isoformat(), ended.isoformat()))
    pdb._conn.commit()
    db_reader.set_setting("ec_trip_energy_enabled", "1")
    db_reader.set_setting("ec_trip_since", (started - timedelta(hours=1)).isoformat())
    return pdb


def _ec(total):
    """A getEC reading shaped like get_energy_breakdown_range's output, or None for a miss."""
    if total is None:
        return None
    return {"driving_kwh": round(total * 0.42, 1), "ac_kwh": round(total * 0.47, 1),
            "other_kwh": round(total * 0.11, 1), "total_kwh": total,
            "driving_pct": 42.0, "ac_pct": 47.0, "other_pct": 11.0}


def _row(pdb):
    return pdb._conn.execute(
        "SELECT ec_kwh, ec_stable, ec_tried, efficiency_kwh_100km, efficiency_soc "
        "FROM trips WHERE id=1").fetchone()


def _run(pdb, monkeypatch, reads):
    """Feed `reads` one per sweep; return the 1-based step at which ec_stable first became 1
    (or None if it never locked)."""
    locked_at = None
    for i, v in enumerate(reads, 1):
        monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e, _v=v: _ec(_v))
        ec_enrich._sweep_now()
        if locked_at is None and _row(pdb)[1] == 1:
            locked_at = i
    return locked_at


# ── the lock must complete on its own in every realistic read pattern ─────────

def test_constant_value_locks_on_second_read(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    assert _run(pdb, monkeypatch, [1.9, 1.9]) == 2


def test_none_between_reads_does_not_reset(tmp_path, monkeypatch):
    """A transient cloud miss bumps ec_tried but keeps the stored value → still locks."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    assert _run(pdb, monkeypatch, [1.9, None, 1.9]) == 3


def test_one_step_boundary_wobble_locks(tmp_path, monkeypatch):
    """THE field bug (166/167): value bounces one 0.1 quantization step. Must converge & lock,
    not spin forever as it did with the old 0.05 tolerance."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    assert _run(pdb, monkeypatch, [1.9, 2.0, 1.9, 2.0]) == 2


def test_wild_oscillation_locks_via_backstop(tmp_path, monkeypatch):
    """Values too far apart to converge still lock via the age backstop (2nd usable read, old
    enough) — enrichment can never get permanently stuck."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    assert _run(pdb, monkeypatch, [1.5, 2.5, 1.5, 2.5]) == 2


def test_young_trip_never_locks_early(tmp_path, monkeypatch):
    """Below the 30-min maturity gate nothing locks, even with identical reads — a still-aggregating
    cloud value must not be frozen (and the efficiency must not be overridden)."""
    pdb = _setup(tmp_path, monkeypatch, age_min=10)
    assert _run(pdb, monkeypatch, [1.9, 1.9, 1.9]) is None
    assert _row(pdb)[3] == pytest.approx(30.0)   # SoC efficiency untouched
    assert _row(pdb)[4] is None                  # no backup taken


# ── manual on-demand conversion (the "Convert with official data" button) ─────

def test_convert_trip_applies_official_even_with_feature_off(tmp_path, monkeypatch):
    """Manual convert is an explicit user action: it ignores the feature flag and the age/maturity
    gates, locks immediately, overrides efficiency and keeps SoC as backup."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    db_reader.set_setting("ec_trip_energy_enabled", "0")   # off → background sweep wouldn't touch it
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: _ec(1.9))
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is True
    ec_kwh, stable, _tried, eff, eff_soc = _row(pdb)
    assert stable == 1 and ec_kwh == pytest.approx(1.9)
    assert eff == pytest.approx(27.1, abs=0.05)
    assert eff_soc == pytest.approx(30.0)


def test_convert_trip_no_cloud_data_changes_nothing(tmp_path, monkeypatch):
    """Old/unresolved trip: the cloud has no data → returns no_data and leaves the trip on SoC."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: None)
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is False and res["reason"] == "no_data"
    ec_kwh, stable, _tried, eff, _soc = _row(pdb)
    assert stable == 0 and ec_kwh is None and eff == pytest.approx(30.0)


def _insert_prev(pdb, *, gap_min):
    """Insert a previous trip (id 2) that ended `gap_min` before trip 1 started."""
    from datetime import datetime, timedelta
    s1 = datetime.fromisoformat(pdb._conn.execute("SELECT started_at FROM trips WHERE id=1").fetchone()[0])
    pe = s1 - timedelta(minutes=gap_min)
    ps = pe - timedelta(minutes=10)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km) "
        "VALUES (2,1,?,?,4.0,20.0)", (ps.isoformat(), pe.isoformat()))
    pdb._conn.commit()


def test_convert_no_data_brief_stop_reports_merged(tmp_path, monkeypatch):
    """No cloud data AND the previous trip ended a brief moment before (< merge default) → one drive
    the cloud merged; suggest merging (distinct reason/message), not the generic 'no data'."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    _insert_prev(pdb, gap_min=2)   # momentary stop → one continuous drive
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: None)
    assert ec_enrich.convert_trip(1)["reason"] == "merged_cloud"


def test_convert_no_data_real_stop_reports_no_data(tmp_path, monkeypatch):
    """A real stop (≥ merge default — shopping, errands) = two separate trips → genuine 'no data',
    NOT a merge suggestion (merging distinct trips makes no sense)."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    _insert_prev(pdb, gap_min=20)   # 20-min stop = a destination, not one drive
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: None)
    assert ec_enrich.convert_trip(1)["reason"] == "no_data"


# ── merge two close trips → convert the COMBINED drive ────────────────────────

def _two_close_trips(tmp_path, monkeypatch):
    """Trip 1 (A, 4 km, 20 kWh/100) then trip 2 (B, 6 km) starting 1 min after A — mergeable."""
    from datetime import datetime, timedelta, timezone
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    a_end = now - timedelta(minutes=140)
    a_start = a_end - timedelta(minutes=10)
    b_start = a_end + timedelta(minutes=1)
    b_end = b_start + timedelta(minutes=15)
    pdb._conn.execute("INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,"
                      "efficiency_kwh_100km,start_soc,end_soc) VALUES (1,1,?,?,4.0,20.0,80,78)",
                      (a_start.isoformat(), a_end.isoformat()))
    pdb._conn.execute("INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,"
                      "efficiency_kwh_100km,start_soc,end_soc) VALUES (2,1,?,?,6.0,25.0,78,75)",
                      (b_start.isoformat(), b_end.isoformat()))
    pdb._conn.commit()
    db_reader.set_setting("ec_trip_energy_enabled", "1")
    db_reader.set_setting("ec_trip_since", (a_start - timedelta(hours=1)).isoformat())
    return pdb


def test_merge_then_convert_uses_combined_drive(tmp_path, monkeypatch):
    pdb = _two_close_trips(tmp_path, monkeypatch)
    assert db_reader.merge_trips(1, 2)["ok"] is True
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: {"driving_kwh": 1.0, "ac_kwh": 0.4, "other_kwh": 0.2,
                                      "total_kwh": 1.6, "driving_pct": 62, "ac_pct": 25, "other_pct": 13})
    assert ec_enrich.convert_trip(1)["ok"] is True
    det = db_reader.get_trip_detail(1)
    assert det["distance_km"] == pytest.approx(10.0)          # combined, not 4
    assert det["ec_kwh"] == pytest.approx(1.6)
    assert det["efficiency_kwh_100km"] == pytest.approx(16.0)  # 1.6 / 10 km, official — not SoC
    assert det["energy_kwh"] == pytest.approx(1.6, abs=0.05)


def test_convert_first_of_brief_split_suggests_merge_not_overattribute(tmp_path, monkeypatch):
    """Converting the EARLIER half of a brief split standalone: the cloud returns the COMBINED energy on
    that trip's own window — applying it over only its distance would overstate it (1.6/4km=40 vs the
    correct 1.6/10km=16). Guard must suggest merge and leave the trip untouched."""
    pdb = _two_close_trips(tmp_path, monkeypatch)   # trip 1 then trip 2, 1 min later (mergeable)
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: {"driving_kwh": 1.0, "ac_kwh": 0.3, "other_kwh": 0.3,
                                      "total_kwh": 1.6, "driving_pct": 62, "ac_pct": 19, "other_pct": 19})
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is False and res["reason"] == "merged_cloud"
    r = pdb._conn.execute("SELECT ec_kwh, ec_stable, round(efficiency_kwh_100km, 1) FROM trips WHERE id=1").fetchone()
    assert r[0] is None and r[1] == 0 and r[2] == 20.0   # untouched — NOT over-attributed to 40


def test_unmerge_clears_combined_ec_and_restores_soc(tmp_path, monkeypatch):
    pdb = _two_close_trips(tmp_path, monkeypatch)
    db_reader.merge_trips(1, 2)
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: {"driving_kwh": 1.0, "ac_kwh": 0.4, "other_kwh": 0.2,
                                      "total_kwh": 1.6, "driving_pct": 62, "ac_pct": 25, "other_pct": 13})
    ec_enrich.convert_trip(1)
    db_reader.unmerge_trip(1)
    a = db_reader.get_trip_detail(1)
    assert a["ec_kwh"] is None and a["ec_stable"] == 0
    assert a["efficiency_kwh_100km"] == pytest.approx(20.0)   # A's own SoC efficiency restored


def test_lock_overrides_efficiency_and_backs_up_soc(tmp_path, monkeypatch):
    """On lock the trip's efficiency becomes the EC-derived figure and the SoC value is preserved
    for a reversible revert."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    assert _run(pdb, monkeypatch, [1.9, 1.9]) == 2
    ec_kwh, stable, _tried, eff, eff_soc = _row(pdb)
    assert stable == 1 and ec_kwh == pytest.approx(1.9)
    assert eff == pytest.approx(27.1, abs=0.05)   # 1.9 / 7 km * 100
    assert eff_soc == pytest.approx(30.0)          # original SoC efficiency kept as backup


# ── #96 regression: an INCOMPLETE cloud value must not overwrite a good estimate ──

def test_convert_trip_subfloor_kept_estimate(tmp_path, monkeypatch):
    """Issue #96: the cloud returns a value, but it's an incomplete aggregation implying an impossible
    efficiency (< _MIN_PLAUSIBLE_EFF). The manual convert must REFUSE it and keep the SoC estimate —
    not lock a wrong figure over the good one (the v1.34.0 bug). Mirrors the auto-sweep's floor."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)        # trip: 7 km, SoC eff 30.0
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: _ec(0.3))               # 0.3 kWh / 7 km = 4.3 kWh/100km < 5
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is False and res["reason"] == "implausible"
    ec_kwh, stable, tried, eff, eff_soc = _row(pdb)
    assert eff == pytest.approx(30.0)   # SoC estimate untouched
    assert eff_soc is None              # no backup taken → nothing was overwritten
    assert ec_kwh is None and stable == 0
    assert tried == 1                   # attempt recorded


def test_convert_trip_riri96_scenario(tmp_path, monkeypatch):
    """Faithful #96 repro: a 33 km drive, real ≈5.8 kWh (17.5 kWh/100km); the cloud returns a partial
    0.5 kWh (→ 1.5 kWh/100km). Convert must keep 17.5, never apply the impossible 1.5."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    ended = datetime.now(timezone.utc) - timedelta(days=8)  # old trip, like riri19's 21 June one
    started = ended - timedelta(minutes=29)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km)"
        " VALUES (1, 1, ?, ?, 33.0, 17.5)", (started.isoformat(), ended.isoformat()))
    pdb._conn.commit()
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(0.5))
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is False and res["reason"] == "implausible"
    assert _row(pdb)[3] == pytest.approx(17.5)   # estimate preserved, not 1.5


def test_convert_trip_low_but_consistent_with_soc_is_accepted(tmp_path, monkeypatch):
    """The two-condition guard must NOT reject a genuinely low-consumption trip (long descent / heavy
    regen). 50 km where SoC says ≈2.0 kWh (4 kWh/100 km); cloud getEC 1.9 kWh → implied 3.8 kWh/100 km
    is below the floor, BUT it's 95% of the physical SoC delta → real, not incomplete → applied."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    ended = datetime.now(timezone.utc) - timedelta(minutes=120)
    started = ended - timedelta(minutes=40)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km)"
        " VALUES (1, 1, ?, ?, 50.0, 4.0)", (started.isoformat(), ended.isoformat()))
    pdb._conn.commit()
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(1.9))
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is True                       # accepted — low but consistent with the battery
    ec_kwh, stable, _tried, eff, eff_soc = _row(pdb)
    assert stable == 1 and ec_kwh == pytest.approx(1.9)
    assert eff == pytest.approx(3.8, abs=0.05)     # 1.9 / 50 km * 100 — the official low figure
    assert eff_soc == pytest.approx(4.0)           # SoC kept as backup


def test_convert_trip_incomplete_caught_via_raw_soc_when_efficiency_missing(tmp_path, monkeypatch):
    """Extra safety layer: even when the stored efficiency is absent (Mate withholds it on net-≤0 / very
    short trips), the guard still catches an incomplete getEC from raw ΔSoC × battery capacity. 33 km
    trip with NO efficiency but SoC 66.3→57.4 (≈5.8 kWh on a 65 kWh pack); cloud 0.5 kWh → rejected."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.set_setting("battery_capacity_kwh", "65.0")
    ended = datetime.now(timezone.utc) - timedelta(days=8)
    started = ended - timedelta(minutes=29)
    pdb._conn.execute(   # efficiency_kwh_100km left NULL on purpose
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (1, 1, ?, ?, 33.0, 66.3, 57.4)", (started.isoformat(), ended.isoformat()))
    pdb._conn.commit()
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(0.5))
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is False and res["reason"] == "implausible"   # caught via raw SoC, no efficiency
    assert _row(pdb)[1] == 0 and _row(pdb)[0] is None              # nothing applied


def test_convert_trip_overattributed_short_kept_estimate(tmp_path, monkeypatch):
    """#98 (mirror of #96): on a very short trip the cloud over-attributes getEC (the window's 2-min
    pre-pad dwarfs the drive) → an impossible efficiency far ABOVE the battery delta. Convert must
    refuse it and keep the estimate. riri19's case: 1 km, SoC 71.0→70.7 (~0.2 kWh), cloud 1.2 kWh."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.set_setting("battery_capacity_kwh", "65.0")
    ended = datetime.now(timezone.utc) - timedelta(minutes=120)
    started = ended - timedelta(minutes=4)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km,"
        " start_soc, end_soc) VALUES (1, 1, ?, ?, 1.0, 19.5, 71.0, 70.7)",
        (started.isoformat(), ended.isoformat()))
    pdb._conn.commit()
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(1.2))
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is False and res["reason"] == "implausible"
    assert _row(pdb)[3] == pytest.approx(19.5)   # estimate kept, not the impossible 120 kWh/100km


def test_convert_trip_high_but_soc_consistent_accepted(tmp_path, monkeypatch):
    """The high-side guard needs BOTH signals: a short trip with a high implied efficiency (>60) but
    whose getEC still matches the battery delta (overshoot < 2×) is real (cold/hard burst), not
    over-attributed → accepted. 1.5 km, SoC 80→79.2 (~0.52 kWh on a 65 kWh pack), cloud 1.0 kWh →
    eff 66.7 kWh/100km but ratio ~1.9 → applied."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.set_setting("battery_capacity_kwh", "65.0")
    ended = datetime.now(timezone.utc) - timedelta(minutes=120)
    started = ended - timedelta(minutes=6)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km,"
        " start_soc, end_soc) VALUES (1, 1, ?, ?, 1.5, 66.7, 80.0, 79.2)",
        (started.isoformat(), ended.isoformat()))
    pdb._conn.commit()
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(1.0))
    res = ec_enrich.convert_trip(1)
    assert res["ok"] is True                       # high but consistent with the battery → applied
    ec_kwh, stable, _t, eff, _soc = _row(pdb)
    assert stable == 1 and ec_kwh == pytest.approx(1.0)
    assert eff == pytest.approx(66.7, abs=0.1)     # 1.0 / 1.5 km * 100


# ── Ready-session window + shared-session detection (the 22/06 case) ──────────

def _ready(pdb, on, off, step=30):
    """Insert positions.ready=1 from `on` to `off` (datetimes), bracketed by ready=0 outside."""
    pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,0)",
                      ((on - timedelta(minutes=2)).isoformat(),))
    t = on
    while t <= off:
        pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,1)",
                          (t.isoformat(),))
        t += timedelta(seconds=step)
    pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,0)",
                      ((off + timedelta(minutes=2)).isoformat(),))
    pdb._conn.commit()


def _two_session_trips(tmp_path, monkeypatch, *, ready_continuous):
    """Trip 1 (4 km) then trip 2 (3 km) 40 min later. If ready_continuous, the car stayed ON the whole
    time (one power-on session spanning both); else it powered off between them (two sessions)."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    aS = now - timedelta(hours=2); aE = aS + timedelta(minutes=15)
    bS = aE + timedelta(minutes=40); bE = bS + timedelta(minutes=12)
    pdb._conn.execute("INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,"
                      "efficiency_kwh_100km,start_soc,end_soc) VALUES (1,1,?,?,4.0,26.0,95,93)",
                      (aS.isoformat(), aE.isoformat()))
    pdb._conn.execute("INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,"
                      "efficiency_kwh_100km,start_soc,end_soc) VALUES (2,1,?,?,3.0,24.0,93,92)",
                      (bS.isoformat(), bE.isoformat()))
    pdb._conn.commit()
    db_reader.set_setting("ec_trip_energy_enabled", "1")
    db_reader.set_setting("ec_trip_since", (aS - timedelta(hours=1)).isoformat())
    if ready_continuous:
        _ready(pdb, aS, bE)                       # one session covering BOTH trips
    else:
        _ready(pdb, aS, aE); _ready(pdb, bS, bE)  # two separate sessions
    return pdb


def test_ready_session_detects_shared_and_blocks_convert(tmp_path, monkeypatch):
    """22/06 case: car never powered off between two trips → ONE Ready session over both → converting
    either alone is blocked with reason 'shared_session' (would grab the whole session)."""
    pdb = _two_session_trips(tmp_path, monkeypatch, ready_continuous=True)
    s = db_reader.ready_session(dict(pdb._conn.execute("SELECT * FROM trips WHERE id=1").fetchone()))
    assert s and s["n_trips"] == 2 and set(s["trip_ids"]) == {1, 2}
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(2.3))
    assert ec_enrich.convert_trip(1)["reason"] == "shared_session"
    assert ec_enrich.convert_trip(2)["reason"] == "shared_session"


def test_separate_sessions_not_blocked(tmp_path, monkeypatch):
    """Same two trips but the car WAS powered off between them → two sessions → NOT shared → each
    converts on its own Ready window."""
    pdb = _two_session_trips(tmp_path, monkeypatch, ready_continuous=False)
    s = db_reader.ready_session(dict(pdb._conn.execute("SELECT * FROM trips WHERE id=1").fetchone()))
    assert s["n_trips"] == 1 and s["trip_ids"] == [1]
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(1.0))
    assert ec_enrich.convert_trip(1)["ok"] is True


def test_merge_shared_session_then_convert_combined(tmp_path, monkeypatch):
    """After merging the two trips of a shared session, the group converts over the COMBINED distance
    (the user's resolution): 2.3 kWh / 7 km = 32.9 kWh/100 km."""
    pdb = _two_session_trips(tmp_path, monkeypatch, ready_continuous=True)
    assert db_reader.merge_trips(1, 2)["ok"] is True
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: {"driving_kwh": 1.3, "ac_kwh": 0.8, "other_kwh": 0.2,
                                      "total_kwh": 2.3, "driving_pct": 57, "ac_pct": 35, "other_pct": 8})
    assert ec_enrich.convert_trip(1)["ok"] is True
    det = db_reader.get_trip_detail(1)
    assert det["distance_km"] == pytest.approx(7.0)
    assert det["ec_kwh"] == pytest.approx(2.3)
    assert det["efficiency_kwh_100km"] == pytest.approx(32.9, abs=0.2)


def test_guards_stay_for_single_trip_even_with_ready_session(tmp_path, monkeypatch):
    """Even on the Ready-session path the plausibility guards STAY for a SINGLE trip — it's about
    correctness, not just the window. A getEC implausible vs the trip's own ΔSoC is rejected → SoC kept:
    HIGH = the session swallowed pre-drive idle/climate, so getEC over-states the drive (drive-only SoC
    is truer); LOW = a genuine cloud gap. (The multi-trip case is handled by the shared-session block.)"""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)   # trip 1, 7 km, SoC eff 30 → ΔSoC ref ≈ 2.1 kWh
    tr = dict(db_reader._get().execute("SELECT * FROM trips WHERE id=1").fetchone())
    _ready(pdb, datetime.fromisoformat(tr["started_at"]), datetime.fromisoformat(tr["ended_at"]))
    # HIGH (5.0 kWh / 7 km = 71 kWh/100km, 2.4× the ΔSoC) → rejected, estimate kept
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(5.0))
    assert ec_enrich.convert_trip(1)["reason"] == "implausible"
    assert _row(pdb)[3] == pytest.approx(30.0)
    # LOW (0.2 kWh, a genuine gap) → also rejected
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(0.2))
    assert ec_enrich.convert_trip(1)["reason"] == "implausible"


def test_trip_ec_window_begins_at_last_off_not_first_on(tmp_path, monkeypatch):
    """#117 regression: the getEC window must begin at on_lo (the LAST ready=0 sample before the
    session), NOT at sess['on'] (the first ready=1 poll). The real power-on (= cloud anchor) sits
    between the two, within one poll interval; sess['on'] can land AFTER the anchor → getEC None and
    the trip wrongly drops to SoC. on_lo is a sample where the car was provably OFF → ≤ the anchor."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    tr = dict(db_reader._get().execute("SELECT * FROM trips WHERE id=1").fetchone())
    on = datetime.fromisoformat(tr["started_at"]); off = datetime.fromisoformat(tr["ended_at"])
    c = pdb._conn
    for dt in (90, 60, 30):                          # off-cadence ready=0 samples before the trip
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,0)",
                  ((on - timedelta(seconds=dt)).isoformat(),))
    t = on
    while t <= off:                                  # ready=1 from the trip start onward
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,1)", (t.isoformat(),))
        t += timedelta(seconds=30)
    c.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,0)",
              ((off + timedelta(minutes=2)).isoformat(),))
    c.commit()
    sess = db_reader.ready_session(tr)
    assert sess["on_lo"] is not None and sess["on_lo"] < sess["on"]          # last off precedes first on
    assert abs(sess["on_lo"] - int((on - timedelta(seconds=30)).timestamp())) <= 1   # = the −30 s sample
    b, e = db_reader.trip_ec_window(tr)
    assert b == sess["on_lo"]                                                # window starts at on_lo …
    assert b < sess["on"]                                                    # … NOT at sess['on'] (#117)


# ── recovery: per-trip "Revert to estimate" ──────────────────────────────────

def test_revert_trip_ec_restores_estimate(tmp_path, monkeypatch):
    """After a conversion, revert restores the backed-up SoC efficiency, drops the EC split, clears the
    lock and parks ec_tried so the sweep won't silently redo it — while a manual re-convert still works."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(1.9))
    assert ec_enrich.convert_trip(1)["ok"] is True
    assert _row(pdb)[1] == 1                        # converted/locked
    assert db_reader.revert_trip_ec(1) is True
    ec_kwh, stable, tried, eff, _soc = _row(pdb)
    assert eff == pytest.approx(30.0)              # SoC estimate restored
    assert stable == 0 and ec_kwh is None
    assert tried >= 80                             # parked past the sweep's give-up threshold
    assert ec_enrich.convert_trip(1)["ok"] is True  # manual re-convert ignores ec_tried


def test_revert_trip_ec_noop_when_not_converted(tmp_path, monkeypatch):
    """Reverting a trip that was never converted (no SoC backup) does nothing and reports False."""
    pdb = _setup(tmp_path, monkeypatch, age_min=120)
    assert db_reader.revert_trip_ec(1) is False
    assert _row(pdb)[3] == pytest.approx(30.0)


# ── beta #19 (@michapr): a READY value may not be carried forward for ever ────
#
# ready_session bridges polls that report no READY value by reusing the last one. That is right for
# a missed reading and wrong for a car that barely reports the signal at all: on his REEV B10 it
# arrives in ~0.8% of frames and effectively never as a zero, so one ready=1 kept meaning "still on"
# for hours and two genuinely separate drives came back as one power-on — which told him to MERGE
# trips that must stay apart. On a BEV the same code is unaffected: 89.8% of position rows carry a
# value and 99.9% of consecutive samples are one poll apart, so the window never expires.

def _sparse_ready(pdb, start, minutes, *, samples_at, step=30):
    """A REEV-shaped log: a poll every `step` seconds for `minutes`, but only the polls listed in
    `samples_at` (seconds from start) actually report READY=1. Everything else is NULL — and a zero
    is never observed, which is the part that made the carry-forward the only source of truth."""
    t, end = start, start + timedelta(minutes=minutes)
    while t <= end:
        off = int((t - start).total_seconds())
        val = 1 if off in samples_at else None
        pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,?)",
                          (t.isoformat(), val))
        t += timedelta(seconds=step)
    pdb._conn.commit()


def _two_trips_only(tmp_path, monkeypatch, aS, aE, bS, bE):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute("INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,"
                      "efficiency_kwh_100km,start_soc,end_soc) VALUES (1,1,?,?,57.0,24.0,87.8,79.6)",
                      (aS.isoformat(), aE.isoformat()))
    pdb._conn.execute("INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,"
                      "efficiency_kwh_100km,start_soc,end_soc) VALUES (2,1,?,?,12.0,24.0,79.5,77.0)",
                      (bS.isoformat(), bE.isoformat()))
    pdb._conn.commit()
    db_reader.set_setting("ec_trip_energy_enabled", "1")
    db_reader.set_setting("ec_trip_since", (aS - timedelta(hours=1)).isoformat())
    return pdb


def test_sparse_ready_does_not_span_a_real_power_off(tmp_path, monkeypatch):
    """His case: READY seen three times at the start of trip A and never again, two trips 78 minutes
    apart with the car off in between. The carry-forward must expire rather than report one session
    over both — that report is what asks the user to merge them."""
    aS = datetime(2026, 7, 28, 7, 56, tzinfo=timezone.utc)
    aE, bS, bE = aS + timedelta(minutes=42), aS + timedelta(minutes=120), aS + timedelta(minutes=150)
    pdb = _two_trips_only(tmp_path, monkeypatch, aS, aE, bS, bE)
    _sparse_ready(pdb, aS, 240, samples_at={60, 180, 300})

    s = db_reader.ready_session(dict(pdb._conn.execute("SELECT * FROM trips WHERE id=1").fetchone()))
    assert s, "trip A's own session should still be recognised from the samples it does have"
    assert 2 not in s["trip_ids"], "the second drive is a separate power-on, not part of this one"
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(2.3))
    assert ec_enrich.convert_trip(1).get("reason") != "shared_session"


def test_a_dense_ready_log_still_reports_a_genuinely_shared_session(tmp_path, monkeypatch):
    """The other side, and the one the fix must not break: a BEV reporting READY every poll, with the
    car left ON across a six-minute stop. That IS one power-on and must still be reported as such —
    measured on 60 real BEV trips, five look like this and all five are genuine."""
    aS = datetime(2026, 7, 28, 7, 56, tzinfo=timezone.utc)
    aE, bS, bE = aS + timedelta(minutes=42), aS + timedelta(minutes=48), aS + timedelta(minutes=70)
    pdb = _two_trips_only(tmp_path, monkeypatch, aS, aE, bS, bE)
    _ready(pdb, aS, bE)                              # continuous ready=1 over both trips

    s = db_reader.ready_session(dict(pdb._conn.execute("SELECT * FROM trips WHERE id=1").fetchone()))
    assert s and set(s["trip_ids"]) == {1, 2}
    monkeypatch.setattr(command_client, "get_energy_breakdown_range", lambda b, e: _ec(2.3))
    assert ec_enrich.convert_trip(1)["reason"] == "shared_session"


def test_the_carry_forward_still_bridges_a_few_missed_polls(tmp_path, monkeypatch):
    """The window exists to survive a gap, not to forbid one: READY reported, then five minutes of
    polls with no value, then reported again. That is one unbroken session."""
    aS = datetime(2026, 7, 28, 7, 56, tzinfo=timezone.utc)
    aE, bS, bE = aS + timedelta(minutes=42), aS + timedelta(minutes=48), aS + timedelta(minutes=70)
    pdb = _two_trips_only(tmp_path, monkeypatch, aS, aE, bS, bE)
    # a value every poll except a 5-minute hole in the middle of trip A
    hole = set(range(1200, 1500, 30))
    _sparse_ready(pdb, aS, 75, samples_at={s for s in range(0, 75 * 60, 30)} - hole)

    s = db_reader.ready_session(dict(pdb._conn.execute("SELECT * FROM trips WHERE id=1").fetchone()))
    assert s and set(s["trip_ids"]) == {1, 2}, "a five-minute gap must not split one power-on"


def test_an_expired_carry_is_unknown_not_an_observed_power_off(tmp_path, monkeypatch):
    """What the window expires INTO matters as much as when. `on_lo` — the last observed ready=0
    before a session — is the primary getEC start, and trip_ec_window trusts it as a moment the car
    was *provably* off. If expiry wrote 0 instead of "no longer known", every long silence would
    manufacture such a moment out of a reading nobody ever took. Here READY is only ever seen as 1,
    so there is no observed zero anywhere and on_lo must stay None."""
    aS = datetime(2026, 7, 28, 7, 56, tzinfo=timezone.utc)
    aE, bS, bE = aS + timedelta(minutes=42), aS + timedelta(minutes=120), aS + timedelta(minutes=150)
    pdb = _two_trips_only(tmp_path, monkeypatch, aS, aE, bS, bE)
    # READY reported only inside trip B, never before it, and never as a zero.
    _sparse_ready(pdb, aS, 240, samples_at={7260, 7380, 7500})

    s = db_reader.ready_session(dict(pdb._conn.execute("SELECT * FROM trips WHERE id=2").fetchone()))
    assert s, "trip B's own samples should still form a session"
    zeros = [r[0] for r in pdb._conn.execute(
        "SELECT recorded_at FROM positions WHERE ready = 0")]
    assert zeros == [], "the fixture must contain no observed zero, or this proves nothing"
    assert s.get("on_lo") is None, "on_lo was invented from an expired carry, not an observed zero"
