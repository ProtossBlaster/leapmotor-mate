"""A park that produced no bar has to say why (#241, @riri19).

He reported: *"Discharge while parked — no data after 05/08"*, while the car went on driving and
parking normally. The bundle could only list the parks that had been ACCEPTED, so from the outside
"the car reported the same SoC for nineteen hours" and "the car was never parked at all" arrived as
the same blank. Two separate theories about that gap were built and both turned out to be wrong —
the second one after seeding it and watching the code handle it correctly.

So the section now lists the rejections too, with the reason. The point is not tidiness: it is that
the next bundle answers the question instead of starting another round of guessing.

⚠️ And the discrimination matters as much as the reasons. A park that shows up as `flat` and a park
that does not show up **at all** are different findings: the first says the samples were parked and
the SoC never moved, the second says the samples were not parked. Today both read as silence.
→ [[signal-absent-is-not-signal-zero]] · [[feedback-verified-vs-inferred]]
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import diagnostics
import pytest

T0 = datetime(2026, 8, 7, 11, 35, tzinfo=timezone.utc)


def _iso(m):
    return (T0 + timedelta(minutes=m)).isoformat()


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "p.db")
    pdb = D.Database(path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST','B10')")
    pdb._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return pdb


def _park(pdb, m_from, m_to, soc_from, soc_to, *, step=30, odo=10718, charging=0):
    n = max(1, int((m_to - m_from) / step))
    for i in range(n + 1):
        m = m_from + i * step
        soc = soc_from + (soc_to - soc_from) * i / n
        pdb._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
            " odometer_km, ready) VALUES (1,?,?,?,0,?,0)",
            (_iso(m), round(soc, 1), charging, odo))
    pdb._conn.commit()


def _reasons(min_hours=8.0, min_drop=0.2):
    v = db_reader.get_vampire_drain(min_hours=min_hours, min_drop_pct=min_drop)
    return v, {r["why"] for r in v["rejected"]}


# ── the three reasons a park can produce nothing ──────────────────────────────

def test_a_park_the_car_reported_flat_is_named(car):
    """riri19's shape: nineteen and a half hours at exactly 48.5 %, the cloud repeating one frame.
    The chart shows nothing — but the park DID happen, and the bundle now says so."""
    _park(car, 0, 1170, 48.5, 48.5)
    v, why = _reasons()
    assert v["count"] == 0, "nothing should be charted — that is the symptom"
    assert why == {"flat"}, why
    r = v["rejected"][0]
    assert r["hours"] == pytest.approx(19.5, abs=0.2)
    assert r["soc_start"] == 48.5 and r["soc_end"] == 48.5


def test_a_park_shorter_than_the_users_threshold_is_named(car):
    """He raised `vampire_min_hours` from 1 to 8. A four-hour stop is then invisible by his own
    choice — which is a perfectly good answer, and one nobody could read before."""
    _park(car, 0, 240, 60.0, 59.0)
    v, why = _reasons(min_hours=8.0)
    assert v["count"] == 0
    assert why == {"short"}, why


def test_a_park_that_barely_moved_is_named(car):
    """Long enough, but the battery moved less than the noise floor: the estimate would be one
    sensor step extrapolated over a day."""
    _park(car, 0, 1170, 60.0, 59.9)
    v, why = _reasons()
    assert v["count"] == 0
    assert why == {"below_noise_floor"}, why


def test_a_park_that_woke_already_driving_is_named_as_such_not_as_flat(car):
    """🔴 This is riri19's second symptom, and reading the new section is what found it.

    A sleeping car reports a FROZEN SoC; the real drain only shows on the first fresh frame. But if
    that frame already carries a higher odometer, the park is closed by the odometer guard without
    the wake-close — rightly, because the drop now contains driving consumption and calling it
    standby would inflate it. What was wrong was the silence, and then the word: the park read as
    "flat" when the truth is "the car had already moved". On a car whose cloud only refreshes once
    it is driving, that is every park, and the chart simply stops."""
    _park(car, 0, 1170, 44.7, 44.7)                        # 19h30, SoC frozen by the cloud
    car._conn.execute(                                     # the wake: fresh SoC AND a new odometer
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
        " odometer_km, ready) VALUES (1,?,44.2,0,40,10723,1)", (_iso(1182),))
    car._conn.commit()
    v, why = _reasons()
    assert v["count"] == 0
    assert why == {"woke_driving"}, why


def test_the_same_park_is_measured_when_the_car_had_not_moved(car):
    """The control that gives the test above its meaning: change only the odometer and the very
    same park is charted, with the drain the wake revealed. Without this pair, "0 windows" proves
    nothing — it could be any of a dozen other reasons."""
    _park(car, 0, 1170, 44.7, 44.7)
    car._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
        " odometer_km, ready) VALUES (1,?,44.2,0,40,10718,1)", (_iso(1182),))
    car._conn.commit()
    v, _why = _reasons()
    assert v["count"] == 1, v["rejected"]
    assert v["windows"][0]["drop_pct"] == pytest.approx(0.5, abs=0.05)


# ── and the discrimination that is the whole point ────────────────────────────

def test_a_stretch_that_was_never_parked_appears_in_neither_list(car):
    """The other half of the answer. If the samples were not idle — charging, driving, V2L — there
    is no park to reject, and the bundle reporting **zero** rejections says exactly that. A reader
    can then tell 'reported flat' from 'never looked parked', which is where I went wrong twice."""
    _park(car, 0, 1170, 60.0, 55.0, charging=1)
    v, _why = _reasons()
    assert v["count"] == 0 and v["rejected"] == [], v["rejected"]


def test_a_good_park_is_still_charted_and_not_listed_as_rejected(car):
    """The control. Without it this file would pass on a build that rejected everything."""
    _park(car, 0, 1170, 60.0, 58.0)
    v, _why = _reasons()
    assert v["count"] == 1, v
    assert v["rejected"] == []


def test_stops_shorter_than_an_hour_are_not_listed(car):
    """A car reports dozens of short stops a day; listing them would bury the one that matters."""
    _park(car, 0, 30, 60.0, 60.0, step=10)
    v, _why = _reasons(min_hours=1.0)
    assert v["rejected"] == [], v["rejected"]


# ── the bundle text itself, which is what actually reaches us ─────────────────

def test_the_bundle_section_prints_the_rejections_and_the_legend(car, monkeypatch):
    """Rendered, not inspected: what triage reads is this text, and a value that never makes it
    into the string is a value we do not have. → [[mate-web-ui-gotchas]]"""
    _park(car, 0, 1170, 48.5, 48.5)
    monkeypatch.setattr(diagnostics.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(diagnostics.db_reader, "get_setting",
                        lambda k, d=None: {"vampire_min_hours": "8", "vampire_min_drop_pct": "0.2"}.get(k, d))
    text = diagnostics._vampire_section()
    assert "parks that produced NO bar: 1" in text, text
    assert "⛔ flat" in text, text
    assert "48.5→48.5" in text, text
    assert "flat=SoC never moved" in text, text          # the legend, so `why` needs no decoder


def test_the_cap_is_declared_when_it_bites(car, monkeypatch):
    """No silent truncation: if more parks were rejected than are shown, the line says so.
    → [[feedback-a-search-needs-an-upper-bound]]"""
    for day in range(70):                                # more than the 60-window cap
        base = day * 24 * 60
        _park(car, base, base + 120, 60.0, 60.0, odo=10000 + day)
    v = db_reader.get_vampire_drain(min_hours=1.0, min_drop_pct=0.2, lookback_days=200)
    assert v["rejected_total"] > len(v["rejected"]), (v["rejected_total"], len(v["rejected"]))
    monkeypatch.setattr(diagnostics.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(diagnostics.db_reader, "get_setting",
                        lambda k, d=None: {"vampire_min_hours": "1", "vampire_min_drop_pct": "0.2"}.get(k, d))
    assert "showing the last" in diagnostics._vampire_section()
