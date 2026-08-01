"""SoH on an LFP pack: don't count the BMS re-anchoring as energy — #205, @riri19.

He has a nine-month-old B10 Max LFP reading **94.9 %** and said the figure was being dragged down
by one charge with a small ΔSoC. He was right, and the same charge exists in Silvio's own history:
a 12.9-point top-up ending at 100 % that estimates the pack at **57.7 kWh** where every other
charge says 64–67.

Two separate causes, and neither is the SoC being "noisy":

1. **The top of the charge is not energy.** Above ~95 % the BMS re-anchors a SoC it has been
   counting (LFP's voltage curve is flat, so it counts coulombs and drifts). Those points arrive
   without matching energy, so `energy / ΔSoC` reads low — and worst on a short top-up, where they
   are most of the delta. The fix is NOT to discard those charges: they are the ones that
   re-calibrate the pack. It is to stop integrating at 95 % and use the part that IS energy.

2. **The average weighted the wrong thing.** It weighted by where a charge ENDED, so a 13-point
   top-up to 100 % carried the same weight as a 57-point charge to 100 %. Summing energy and SoC
   across the window instead makes each charge count in proportion to how much scale it actually
   covered — riri19's own suggestion — and throws nothing away.

And the third piece is honesty rather than arithmetic: the result is still `measured energy ÷
counted SoC`. Removing a known systematic error makes it steadier, not true, so the page shows the
scatter next to the number instead of a bare figure that looks like a lab measurement.
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader


def _charge(pdb, cid, start_soc, samples, kw=7.0, volts=230.0):
    """A charge whose SoC walks through `samples`, one row a minute at constant power."""
    t0 = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc) + timedelta(days=cid)
    amps = kw * 1000.0 / volts
    pdb._conn.execute(
        "INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type) "
        "VALUES (?,1,?,?,?,?,'AC')",
        (cid, t0.isoformat(), (t0 + timedelta(minutes=len(samples))).isoformat(),
         start_soc, samples[-1]))
    for i, soc in enumerate(samples):
        pdb._conn.execute(
            "INSERT INTO positions (vehicle_id,recorded_at,soc,charging,charge_voltage_v,"
            "charge_current_a,battery_min_temp) VALUES (1,?,?,1,?,?,20.0)",
            ((t0 + timedelta(minutes=i)).isoformat(), soc, volts, amps))
    pdb._conn.commit()


def _minutes_for(points_of_soc, kwh_per_point, kw=7.0):
    """How many minutes at `kw` deliver the energy a real pack would take for that many points."""
    return int(round(points_of_soc * kwh_per_point / kw * 60))


def _pack(pdb, cid, start, end, *, real_kwh_per_point, phantom_points=0):
    """A charge that really stores `real_kwh_per_point` per SoC point, plus `phantom_points`
    tacked on at the top for free — the BMS re-anchor."""
    span = end - start - phantom_points
    mins = _minutes_for(span, real_kwh_per_point)
    samples = [start + (span * i / mins) for i in range(mins)]
    samples += [end - phantom_points + (phantom_points * (i + 1) / 3) for i in range(3)]
    _charge(pdb, cid, start, [round(s, 1) for s in samples])


def _db(tmp_path, monkeypatch, nominal=67.1):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES "
                      "('battery_capacity_nominal_kwh', ?)", (str(nominal),))
    pdb._conn.commit()
    return pdb


# ── 1. the top of a charge stops poisoning it ─────────────────────────────────
def test_the_bms_re_anchor_at_the_top_is_not_counted_as_energy(tmp_path, monkeypatch):
    """A pack of 0.67 kWh per point (67 kWh) charged 40 → 100 %, where the last 4 points are the
    BMS catching up rather than energy. Counting them says 62.5 kWh; ignoring them says ~67."""
    pdb = _db(tmp_path, monkeypatch)
    _pack(pdb, 1, 40, 100, real_kwh_per_point=0.67, phantom_points=4)
    h = db_reader.get_battery_health()
    est = h["points"][0]["capacity_kwh"]
    assert est > 65.0, "the phantom points at the top are still being divided into the energy"


# ── 2. the small top-up that riri19 spotted ───────────────────────────────────
def test_a_short_top_up_to_full_no_longer_produces_a_wild_estimate(tmp_path, monkeypatch):
    """87 → 100 % on a healthy pack. Almost all of that delta is the re-anchor, so once the top is
    excluded there is not enough scale left to say anything — and saying nothing is the right
    answer, not 57.7 kWh."""
    pdb = _db(tmp_path, monkeypatch)
    _pack(pdb, 1, 87, 100, real_kwh_per_point=0.67, phantom_points=4)
    h = db_reader.get_battery_health()
    ests = [p["capacity_kwh"] for p in h["points"]]
    assert not any(e < 60.0 for e in ests), "a top-up still estimates the pack far too small"


# ── 3. a big charge outweighs a small one, by construction ────────────────────
def test_a_large_charge_carries_more_weight_than_a_small_one(tmp_path, monkeypatch):
    """riri19's point 3, stated as what pooling actually promises: not immunity to a bad charge,
    but that each one counts in proportion to the scale it covered. A 50-point charge and a
    13-point one that reads low must NOT get an equal say, which is what the old weighting gave
    them — it weighted where a charge ended, so a top-up to 100 % outranked a deep charge."""
    pdb = _db(tmp_path, monkeypatch)
    _pack(pdb, 1, 30, 80, real_kwh_per_point=0.67)          # 50 points, honest
    _charge(pdb, 2, 60, [60 + i * 13 / 40 for i in range(40)] + [73.0], kw=7.0)  # 13 pts, low
    h = db_reader.get_battery_health()
    big, small = (p["capacity_kwh"] for p in sorted(h["points"], key=lambda x: -x["soc_delta_used"]))
    headline = h["latest_capacity_kwh"]
    plain_mean = (big + small) / 2
    assert abs(headline - big) < abs(plain_mean - big), \
        "the small charge still counts as much as the large one"
    # 50 points against 13: the large charge should carry roughly four fifths of the answer.
    assert headline > small + 0.7 * (big - small)


# ── 4. the number no longer pretends to be a measurement ──────────────────────
def test_the_headline_carries_its_own_scatter(tmp_path, monkeypatch):
    pdb = _db(tmp_path, monkeypatch)
    for cid, (a, b) in enumerate(((30, 80), (20, 75), (35, 85)), start=1):
        _pack(pdb, cid, a, b, real_kwh_per_point=0.67)
    h = db_reader.get_battery_health()
    assert h.get("latest_spread_kwh") is not None, "no scatter reported next to the figure"
    assert h["latest_spread_kwh"] >= 0.0


def test_a_single_charge_reports_no_scatter_rather_than_zero(tmp_path, monkeypatch):
    """One point has no spread to speak of. Printing '± 0.0' would be the false precision riri19
    asked us to stop showing."""
    pdb = _db(tmp_path, monkeypatch)
    _pack(pdb, 1, 30, 80, real_kwh_per_point=0.67)
    h = db_reader.get_battery_health()
    assert h["sample_count"] == 1
    assert h.get("latest_spread_kwh") is None
