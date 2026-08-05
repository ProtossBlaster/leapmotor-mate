"""Two figures on the Statistics page that could not be traced back to anything (@michapr, beta #24).

He read **13.9 kWh/100km** at the top and **9.6** in the card below, and **37.85 kWh** of energy used
that matched none of the components he could see. All three were right, and none of them said what
they were about:

  · 13.9 is the average over the kilometres that HAVE a consumption figure. On a range-extender Mate
    blanks that for every trip the generator ran, so it covered 272 of his 434 km.
  · 9.6 is the car's own measured energy over ALL of them.
  · 37.85 was `SUM(distance × COALESCE(efficiency, 0))` — the generator trips contributing **zero
    kWh** rather than being left out, against his own SUM(ec_kwh) of 41.6.

The arithmetic checks out: 13.9 × 272.3 / 100 = 37.85. Two cards, one hidden denominator, and no
way in from the screen.
"""
import json
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATS = (ROOT / "web" / "templates" / "statistics.html").read_text()
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','B10')")
    pdb._conn.commit()
    return pdb


def _trip(pdb, tid, km, *, eff=None, ec=None, stable=1):
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " efficiency_kwh_100km, ec_kwh, ec_stable) VALUES (?,1,?,?,?,80,70,?,?,?)",
        (tid, f"2026-07-{tid:02d}T08:00:00+00:00", f"2026-07-{tid:02d}T09:00:00+00:00",
         km, eff, ec, stable))
    pdb._conn.commit()


# ── a trip with no figure is left out, not counted as nothing ─────────────────

def test_a_trip_with_no_energy_figure_no_longer_counts_as_zero(env):
    """The whole defect. A generator trip has no efficiency by design, and `COALESCE(…, 0)` turned
    that into "used no electricity" — silently dragging the total down."""
    _trip(env, 1, 100.0, eff=15.0)          # 15.0 kWh
    _trip(env, 2, 100.0, ec=12.0)           # no efficiency, but the car measured 12.0
    assert db_reader.get_stats_summary()["total_kwh_used"] == 27.0


def test_the_cars_own_figure_is_the_fallback_and_not_the_preference(env):
    """The efficiency already reflects the owner's setting about whether getEC becomes a trip's
    energy. Preferring ec_kwh over it would overrule that choice — measured on a real BEV, the total
    moved 338.75 → 349.21 kWh for nobody's benefit."""
    _trip(env, 1, 100.0, eff=15.0, ec=17.0)
    assert db_reader.get_stats_summary()["total_kwh_used"] == 15.0


def test_an_unstable_reading_is_not_used(env):
    """`ec_stable = 0` is the flag that says the cloud's figure did not settle. Same guard as
    everywhere else that reads ec_kwh."""
    _trip(env, 1, 100.0, ec=12.0, stable=0)
    assert not db_reader.get_stats_summary()["total_kwh_used"]


def test_it_says_how_much_of_the_driving_it_speaks_for(env):
    _trip(env, 1, 100.0, eff=15.0)
    _trip(env, 2, 100.0)                    # neither figure — genuinely unknown
    s = db_reader.get_stats_summary()
    assert s["energy_trips"] == 1 and s["trip_count"] == 2
    assert s["energy_km"] == 100.0 and s["total_km"] == 200.0


def test_a_zero_kilometre_trip_does_not_earn_the_note(env):
    """Measured on a real BEV: 338 of 340 trips carried a figure, and the two that did not were
    0.0 km. Counting trips would have printed "338 of 340" for ever, about driving that never
    happened — so the note is gated on the DISTANCE missing, and only says the count."""
    _trip(env, 1, 100.0, eff=15.0)
    _trip(env, 2, 0.0)
    s = db_reader.get_stats_summary()
    assert s["energy_trips"] < s["trip_count"]
    assert (s["total_km"] - s["energy_km"]) <= 0.5, "no real kilometres are missing"


def test_a_bev_where_every_trip_has_a_figure_says_nothing(env):
    _trip(env, 1, 100.0, eff=15.0)
    _trip(env, 2, 50.0, eff=16.0)
    s = db_reader.get_stats_summary()
    assert s["energy_trips"] == s["trip_count"] == 2


# ── and the average says what it is averaged over ─────────────────────────────

def test_the_average_reports_the_kilometres_behind_it(env):
    """13.9 over 272 km is a different statement from 13.9 over 434, and the card made the second."""
    _trip(env, 1, 100.0, eff=20.0)
    _trip(env, 2, 300.0)                    # generator trip: no efficiency to average
    s = db_reader.get_stats_summary()
    assert s["avg_efficiency"] == 20.0 and s["avg_efficiency_km"] == 100.0
    assert s["total_km"] == 400.0


def test_his_numbers_reconcile(env):
    """@michapr's own arithmetic, reproduced: 13.9 × 272.3 / 100 = 37.85. If those two cards ever
    stop agreeing with each other, one of them has changed basis without the other."""
    _trip(env, 1, 272.3, eff=13.9)
    _trip(env, 2, 161.7, ec=3.75)
    s = db_reader.get_stats_summary()
    assert s["avg_efficiency_km"] == 272.3
    assert round(s["avg_efficiency"] * s["avg_efficiency_km"] / 100, 2) == 37.85
    assert s["total_kwh_used"] == round(37.85 + 3.75, 2)


# ── how the page says it ──────────────────────────────────────────────────────

def test_the_average_carries_its_kilometres_on_screen():
    assert "stats_eff_over_km" in STATS and "stats_eff_help" in STATS


def test_the_note_is_hidden_when_it_would_say_nothing():
    """A BEV covers all its kilometres; repeating the total under the average would be noise."""
    assert "(totals.total_km - totals.avg_efficiency_km) > 0.5" in STATS
    assert "(totals.total_km - totals.energy_km) > 0.5" in STATS


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_every_language_has_the_words(path):
    d = json.loads(path.read_text())["translations"]
    assert "{n}" in d["stats_energy_partial"] and "{tot}" in d["stats_energy_partial"]
    assert "{km}" in d["stats_eff_over_km"]
    assert d["stats_eff_help"]


def test_the_average_says_WHY_on_a_range_extender():
    """@michapr: "would suggest to write 'over battery-only kilometres' — then we will remember about
    it, and new users can understand it better". Right on a range-extender, and wrong on a
    full-electric car, where the missing kilometres are just trips with no figure. So the page picks
    the wording, rather than one label that would be untrue in one of the two places."""
    assert "stats_eff_over_km_reev" in STATS
    assert "if is_reev else t('stats_eff_over_km')" in STATS


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_range_extender_wording_exists_everywhere(path):
    d = json.loads(path.read_text())["translations"]
    assert "{km}" in d["stats_eff_over_km_reev"]
