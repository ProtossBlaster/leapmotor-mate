"""The period cards stop trusting a cloud total that is missing sessions (#212 @riri19).

The car's own getEC is only as complete as its uplink was. On a car that often can't reach the
cloud while driving, whole sessions are absent from the period total — and the period cards used to
print it anyway, while the Trips page (which falls back to the SoC estimate per trip) showed a
figure a quarter higher, both labelled "average consumption".

Only the LOW side is guarded: a cloud total ABOVE the local sum is normal, it carries climate and
standby energy that no trip is charged with.
"""
# The guard lives in db_reader, not main: `main` pulls in fastapi, which the minimal CI
# test env doesn't install — importing it here would skip these tests exactly where they
# matter most, and a top-level import would kill the whole run at collection.
import db_reader


def _eb(total_kwh):
    return {"total_kwh": total_kwh, "driving_pct": 60, "ac_pct": 30, "other_pct": 10}


def _tot(km, kwh, minutes=120):
    return {"trip_count": 3, "distance_km": km, "duration_min": minutes, "energy_kwh": kwh}


def test_a_short_cloud_total_is_replaced_by_mates_own(monkeypatch):
    # riri19's 1 August: cloud 27.1 kWh over 221 km against the 36.3 his trips add up to.
    eb = db_reader.flag_short_cloud_total(_eb(27.1), _tot(221.0, 36.3), 221.0)
    assert eb["cloud_short"] is True
    assert eb["cloud_total_kwh"] == 27.1        # the cloud's own figure is kept, not lost
    assert eb["local_kwh"] == 36.3
    assert eb["local_avg_kwh100"] == 16.4       # what the Trips page shows for that day


def test_a_healthy_month_is_left_alone(monkeypatch):
    # The three months measured on a car with a good uplink: cloud/local 0.895, 1.032, 0.982.
    for cloud, local, km in ((40.1, 44.8, 281.0), (181.5, 175.8, 908.2), (110.3, 112.3, 654.6)):
        eb = db_reader.flag_short_cloud_total(_eb(cloud), _tot(km, local), km)
        assert "cloud_short" not in eb, f"false positive at {cloud}/{local}"
        assert eb["total_kwh"] == cloud


def test_a_cloud_total_above_the_local_sum_is_never_touched(monkeypatch):
    # Climate and standby energy live in the cloud figure and in no trip — higher is expected.
    eb = db_reader.flag_short_cloud_total(_eb(60.0), _tot(200.0, 30.0), 200.0)
    assert "cloud_short" not in eb


def test_a_window_too_small_to_judge_is_left_alone(monkeypatch):
    # A school run is not evidence about an uplink: the ratio is noise below the floors.
    eb = db_reader.flag_short_cloud_total(_eb(0.2), _tot(8.0, 2.0), 8.0)          # 8 km
    assert "cloud_short" not in eb
    eb = db_reader.flag_short_cloud_total(_eb(0.2), _tot(60.0, 1.5), 60.0)        # 1.5 kWh
    assert "cloud_short" not in eb


def test_no_local_energy_means_no_verdict(monkeypatch):
    # Trips with no efficiency at all sum to 0 — nothing to compare the cloud against, so the
    # cloud figure stands rather than being "corrected" toward zero.
    eb = db_reader.flag_short_cloud_total(_eb(25.0), _tot(300.0, 0.0), 300.0)
    assert "cloud_short" not in eb
    assert eb["total_kwh"] == 25.0


def test_the_boundary_is_where_it_was_measured(monkeypatch):
    # Exactly at the ratio → not short (>= keeps it). A hair below → short.
    at   = db_reader.flag_short_cloud_total(_eb(80.0), _tot(500.0, 100.0), 500.0)
    below = db_reader.flag_short_cloud_total(_eb(79.9), _tot(500.0, 100.0), 500.0)
    assert "cloud_short" not in at
    assert below["cloud_short"] is True


def test_trip_totals_expose_the_local_energy(tmp_path, monkeypatch):
    # The guard's reference comes from the DB, not from the caller — check the query really adds
    # distance x efficiency, and that a trip with no efficiency counts as zero (never negative,
    # never NULL-propagating, so the guard can only err toward leaving the cloud alone).
    from datetime import datetime, timezone
    import db as D
    import db_reader

    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    ts = "2026-08-01T12:00:00+00:00"
    for tid, dist, eff in ((1, 100.0, 20.0), (2, 50.0, 10.0), (3, 25.0, None)):
        pdb._conn.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
            " start_soc, end_soc, efficiency_kwh_100km, regen_kwh, duration_min)"
            " VALUES (?,1,?,?,?,60,50,?,0,30)", (tid, ts, ts, dist, eff))
    pdb._conn.commit()

    b = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
    e = int(datetime(2026, 8, 2, tzinfo=timezone.utc).timestamp())
    tot = db_reader.get_trip_totals_between(b, e)
    assert tot["distance_km"] == 175.0
    assert tot["energy_kwh"] == 25.0          # 20 + 5 + 0
    assert tot["trip_count"] == 3


# ── the two tiles that show it, rendered for real ───────────────────────────────
import pathlib

import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partials")
TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"


def _env():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["nice"] = lambda v: v
    env.filters["dist"] = lambda v, n=0: f"{v} km"
    env.filters["eff"] = lambda v: f"{v} kWh/100km"
    return env


def _render(name, **ctx):
    return _env().get_template(name).render(
        t=lambda k: k, eff_val=lambda v: v, eff_unit=lambda: "kWh/100km",
        fmt_dur=lambda v: f"{v} min", **ctx)


def _short_eb():
    return db_reader.flag_short_cloud_total(_eb(27.1), _tot(221.0, 36.3), 221.0) | {
        "distance_km": 221.0, "duration_min": 175, "avg_kwh100": 12.3}


def test_the_report_tiles_show_mates_figure_and_say_so():
    html = _render("partials/report_driving_energy.html", eb=_short_eb())
    assert "36.3" in html and "16.4" in html      # Mate's own, matching the Trips page
    assert "12.3" not in html                     # not the cloud's average
    assert "ec_cloud_short" in html               # ...and it explains which one this is


def test_the_report_tiles_are_untouched_when_the_cloud_is_fine():
    eb = db_reader.flag_short_cloud_total(_eb(110.3), _tot(654.6, 112.3), 654.6)
    eb = eb | {"distance_km": 654.6, "duration_min": 2373, "avg_kwh100": 16.8}
    html = _render("partials/report_driving_energy.html", eb=eb)
    assert "110.3" in html and "16.8" in html
    assert "ec_cloud_short" not in html


def test_the_split_card_keeps_the_cloud_numbers_but_flags_them():
    # The Guida/Clima/Altro split exists only in the cloud's own figures, so it stays cloud-side —
    # mixing it with a local total would make the percentages describe a number they don't add to.
    html = _render("partials/energy_breakdown.html", eb=_short_eb(), eb_label="Agosto")
    assert "27.1" in html                         # the cloud total is still what's shown
    assert "ec_cloud_short_split" in html         # with a line saying it covers only part
