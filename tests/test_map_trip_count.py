"""The Map's "trips shown" box (get_all_track's max_trips) — same box-on-the-map,
POST-Redirect-GET convention as the neighboring "stations shown" box (map_station_top_n,
see test_trip_charges_and_map_count.py), extended to cap the number of trip ROUTES drawn.

A long driving history otherwise leaves the whole map a solid mess of hundreds of overlapping
lines — and since the point BUDGET (max_points) is shared across however many trips are drawn,
capping the trip COUNT also gives each kept trip more of that budget, so its own line survives
downsampling closer to the actually-driven road.
"""
import pytest

import db as D
import db_reader


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return pdb


def _trip_with_track(pdb, tid, started_at, pts):
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at) VALUES (?,1,?,?)",
        (tid, started_at, started_at))
    pdb._conn.executemany(
        "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) VALUES (?,?,?,?)",
        [(tid, f"{started_at[:10]}T{10+i:02d}:00:00+00:00", lat, lon) for i, (lat, lon) in enumerate(pts)])
    pdb._conn.commit()


# ── get_all_track(max_trips=...) ───────────────────────────────────────────────

def test_max_trips_keeps_only_the_most_recently_started(env):
    pdb = env
    _trip_with_track(pdb, 1, "2026-06-01T10:00:00+00:00", [(45.0, 9.0), (45.001, 9.001)])
    _trip_with_track(pdb, 2, "2026-06-02T10:00:00+00:00", [(46.0, 9.0), (46.001, 9.001)])
    _trip_with_track(pdb, 3, "2026-06-03T10:00:00+00:00", [(47.0, 9.0), (47.001, 9.001)])

    tracks = db_reader.get_all_track(max_trips=2)
    assert len(tracks) == 2
    kept_lats = {tuple(run["points"][0]) for t in tracks for run in t}
    assert (45.0, 9.0) not in kept_lats                  # the oldest trip, dropped
    assert (46.0, 9.0) in kept_lats and (47.0, 9.0) in kept_lats


def test_zero_or_none_means_every_trip(env):
    pdb = env
    for i in range(5):
        _trip_with_track(pdb, i + 1, f"2026-06-0{i+1}T10:00:00+00:00", [(45.0 + i, 9.0), (45.0 + i, 9.001)])
    assert len(db_reader.get_all_track(max_trips=0)) == 5
    assert len(db_reader.get_all_track(max_trips=None)) == 5


def test_max_trips_gives_each_kept_trip_more_of_the_point_budget(env):
    """The whole reason to cap the trip COUNT rather than just the point budget: fewer trips
    sharing the same max_points means each one keeps more points, not just fewer trips at the
    same thinning ratio."""
    pdb = env
    for i in range(10):
        pts = [(45.0 + i, 9.0 + j * 0.0001) for j in range(50)]
        _trip_with_track(pdb, i + 1, f"2026-06-{10+i:02d}T10:00:00+00:00", pts)

    all_tracks = db_reader.get_all_track(max_points=100, max_trips=None)
    capped_tracks = db_reader.get_all_track(max_points=100, max_trips=2)
    pts_per_trip_all = sum(len(run["points"]) for t in all_tracks for run in t) / len(all_tracks)
    pts_per_trip_capped = sum(len(run["points"]) for t in capped_tracks for run in t) / len(capped_tracks)
    assert pts_per_trip_capped > pts_per_trip_all


# ── the Map's "trips shown" box (save_map_trip_count) ─────────────────────────

class _Form:
    def __init__(self, value):
        self._v = value
        self.headers = {}

    async def form(self):
        return {"trips": self._v}


def _save(value):
    import asyncio
    import main
    return asyncio.run(main.save_map_trip_count(_Form(value)))


def test_the_trip_count_is_saved_and_redirects_back_to_the_map(env):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    resp = _save("3")
    assert db_reader.get_setting("map_trips_shown") == "3"
    assert resp.status_code == 303                     # POST-Redirect-GET, not a re-postable page
    assert resp.headers["location"].endswith("/map")


def test_zero_means_all_of_them(env):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    _save("0")
    assert db_reader.get_setting("map_trips_shown") == "0"


def test_a_hand_typed_number_is_clamped(env):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    _save("999999999")
    assert db_reader.get_setting("map_trips_shown") == "9999"
    _save("-5")
    assert db_reader.get_setting("map_trips_shown") == "0"


def test_garbage_falls_back_to_zero_not_a_crash(env):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    _save("abc")
    assert db_reader.get_setting("map_trips_shown") == "0"


def test_drawing_the_map_never_writes_the_trip_count_setting():
    """Same convention as the station-count box: rendering must only READ. A bookmarked map
    URL, a Back button, or a prefetch must never rewrite a stored preference as a side effect."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    import inspect

    import main
    src = inspect.getsource(main.map_page)
    assert src.count("set_setting") == 0, "the map page writes a setting while rendering"
    assert "trips" not in inspect.signature(main.map_page).parameters


def test_the_new_strings_exist_in_every_locale():
    import json
    import pathlib
    loc = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT", "nl"):
        d = json.loads((loc / f"{lang}.json").read_text(encoding="utf-8"))["translations"]
        for key in ("map_trip_count_label", "map_trip_count_hint"):
            assert key in d, f"{lang} is missing {key}"


def test_the_trip_count_form_posts_rather_than_gets():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "web" / "templates" / "map.html").read_text()
    form = src[src.index('<form', src.index("map_trip_count_label") - 400):]
    assert 'method="post"' in form[:form.index(">") + 1]
