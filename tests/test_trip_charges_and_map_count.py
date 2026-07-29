"""The trip's own charge markers, the ←/→ trip navigation, and the Map's station count (#195).

Two of these guard mistakes that were actually made and measured on real data, not hypotheticals:

* A charge the car reported with **no GPS fix** is stored as 0,0 — which is NOT NULL. Filtering
  on `IS NOT NULL` alone let one through and dropped a marker in the Gulf of Guinea, 5 132 km
  from the trip it was attached to. The Map's own cluster has always used the falsy test
  (`not lat or not lon`); this is the same guard, and these tests exist so it stays.
* The station-count box first shipped as a GET that wrote a stored setting. A GET that writes a
  preference is re-applied by every bookmark, Back button and prefetch that touches the URL —
  and on a shared install, one person's link changes everyone's map.
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
    # No learned wallbox: a charge is "home" here only when it says so, which keeps these tests
    # about the window and the coordinates rather than about the home-radius heuristic.
    monkeypatch.setattr(db_reader, "_learned_wallbox_location", lambda vid: None)

    def trip(tid, start, end, merged_into=None):
        pdb._conn.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, merged_into_id) VALUES (?,1,?,?,?)",
            (tid, start, end, merged_into))
        pdb._conn.commit()

    def charge(cid, start, lat=45.5, lon=9.2, location_type="FAST"):
        pdb._conn.execute(
            "INSERT INTO charges (id, vehicle_id, started_at, ended_at, latitude, longitude,"
            " location_type, energy_added_kwh) VALUES (?,1,?,?,?,?,?,20.0)",
            (cid, start, start.replace("T10", "T11"), lat, lon, location_type))
        pdb._conn.commit()

    return trip, charge


def _stop_charges(ended_at):
    return db_reader._trip_stop_charges(db_reader._get(), 1, ended_at)


# ── which charges belong to a trip's stop ─────────────────────────────────────

def test_a_charge_at_null_island_earns_no_marker(env):
    """THE regression. 0,0 is what the car reports when it has no GPS fix — it is not NULL, and
    letting it through put a marker 5 132 km away from a trip that never left Milan."""
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T09:00:00+00:00")
    charge(10, "2026-03-15T10:00:00+00:00", lat=0.0, lon=0.0)

    assert _stop_charges("2026-03-15T09:00:00+00:00") == []


def test_a_charge_with_only_one_coordinate_missing_earns_no_marker(env):
    """Half a fix is no fix: a marker needs both, and 0 counts as absent on either side."""
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T09:00:00+00:00")
    charge(10, "2026-03-15T10:00:00+00:00", lat=45.5, lon=0.0)
    charge(11, "2026-03-15T10:30:00+00:00", lat=0.0, lon=9.2)

    assert _stop_charges("2026-03-15T09:00:00+00:00") == []


def test_a_real_charge_in_the_stop_gets_its_marker(env):
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T09:00:00+00:00")
    charge(10, "2026-03-15T10:00:00+00:00")

    got = _stop_charges("2026-03-15T09:00:00+00:00")
    assert [c["id"] for c in got] == [10]
    assert got[0]["kwh"] == 20.0


def test_a_home_charge_does_not_make_the_driveway_a_charging_stop(env):
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T09:00:00+00:00")
    charge(10, "2026-03-15T10:00:00+00:00", location_type="HOME")

    assert _stop_charges("2026-03-15T09:00:00+00:00") == []


def test_a_charge_after_the_next_trip_started_belongs_to_that_one(env):
    """The window closes when the car moves again — otherwise every later charge would pile
    onto the same trip."""
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T09:00:00+00:00")
    trip(2, "2026-03-15T12:00:00+00:00", "2026-03-15T13:00:00+00:00")
    charge(10, "2026-03-15T10:00:00+00:00")   # in the gap → trip 1
    charge(11, "2026-03-15T14:00:00+00:00")   # after trip 2 left → not trip 1's

    assert [c["id"] for c in _stop_charges("2026-03-15T09:00:00+00:00")] == [10]


def test_a_charge_before_the_trip_ended_is_not_its_stop(env):
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T09:00:00+00:00")
    charge(10, "2026-03-15T07:00:00+00:00")

    assert _stop_charges("2026-03-15T09:00:00+00:00") == []


def test_the_last_trip_keeps_collecting_until_the_car_moves_again(env):
    """Deliberate: with no next trip there is no upper bound, so tonight's charge shows on the
    drive that got the car there — which is exactly the trip it belongs to."""
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T09:00:00+00:00")
    charge(10, "2026-03-18T10:00:00+00:00")   # three days later, car never moved

    assert [c["id"] for c in _stop_charges("2026-03-15T09:00:00+00:00")] == [10]


def test_a_trip_still_running_has_no_stop_yet(env):
    trip, charge = env
    assert _stop_charges(None) == []


# ── ← / → through the trips ───────────────────────────────────────────────────

def test_the_arrows_step_through_top_level_trips(env):
    trip, charge = env
    for i, h in enumerate(("08", "10", "12"), start=1):
        trip(i, f"2026-03-15T{h}:00:00+00:00", f"2026-03-15T{h}:30:00+00:00")

    assert db_reader.get_adjacent_trips(1) == {"prev_id": None, "next_id": 2}
    assert db_reader.get_adjacent_trips(2) == {"prev_id": 1, "next_id": 3}
    assert db_reader.get_adjacent_trips(3) == {"prev_id": 2, "next_id": None}


def test_a_merged_child_steps_from_where_its_parent_sits(env):
    """A child has no page of its own — it shows the parent — so its arrows must be the
    parent's, or ← would land somewhere that isn't previous in the Trips list."""
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T08:30:00+00:00")
    trip(2, "2026-03-15T10:00:00+00:00", "2026-03-15T10:30:00+00:00")
    trip(3, "2026-03-15T10:40:00+00:00", "2026-03-15T11:00:00+00:00", merged_into=2)
    trip(4, "2026-03-15T12:00:00+00:00", "2026-03-15T12:30:00+00:00")

    assert db_reader.get_adjacent_trips(3) == db_reader.get_adjacent_trips(2)
    assert db_reader.get_adjacent_trips(3) == {"prev_id": 1, "next_id": 4}


def test_the_arrows_never_land_on_a_merged_child(env):
    trip, charge = env
    trip(1, "2026-03-15T08:00:00+00:00", "2026-03-15T08:30:00+00:00")
    trip(2, "2026-03-15T09:00:00+00:00", "2026-03-15T09:30:00+00:00", merged_into=1)
    trip(3, "2026-03-15T10:00:00+00:00", "2026-03-15T10:30:00+00:00")

    assert db_reader.get_adjacent_trips(1)["next_id"] == 3      # skips the child at 09:00


def test_a_trip_that_does_not_exist_gets_no_arrows(env):
    trip, charge = env
    assert db_reader.get_adjacent_trips(999) == {"prev_id": None, "next_id": None}


# ── the Map's "stations shown" box ────────────────────────────────────────────

class _Form:
    def __init__(self, value):
        self._v = value
        self.headers = {}

    async def form(self):
        return {"top_n": self._v}


def _save(env, value):
    import asyncio
    import main
    return asyncio.run(main.save_map_station_count(_Form(value)))


def test_the_count_is_saved_and_redirects_back_to_the_map(env):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    resp = _save(env, "3")
    assert db_reader.get_setting("map_station_top_n") == "3"
    assert resp.status_code == 303                    # POST-Redirect-GET, not a re-postable page
    assert resp.headers["location"].endswith("/map")


def test_zero_means_all_of_them(env):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    _save(env, "0")
    assert db_reader.get_setting("map_station_top_n") == "0"


def test_a_hand_typed_number_is_clamped(env):
    """The box is the only way in, but the twin setting (map-threshold) clamps 1–10 and this
    should not store 99999999 verbatim just because nobody can normally type it."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    _save(env, "99999999")
    assert db_reader.get_setting("map_station_top_n") == "999"
    _save(env, "-5")
    assert db_reader.get_setting("map_station_top_n") == "0"


def test_garbage_falls_back_to_the_default(env):
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    _save(env, "abc")
    assert db_reader.get_setting("map_station_top_n") == "15"


def test_drawing_the_map_never_writes_the_setting():
    """It shipped as `/map?top_n=N`, which saved on a page load. Rendering must only READ:
    a bookmarked map URL, a Back, or a prefetch would otherwise rewrite the preference."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    import inspect

    import main
    src = inspect.getsource(main.map_page)
    assert "set_setting" not in src, "the map page writes a setting while rendering"
    assert "top_n" not in inspect.signature(main.map_page).parameters


def test_the_new_strings_exist_in_every_locale():
    """Read the FILES: i18n.get_t falls back to English, so `t(key) != key` passes on a locale
    that never got the translation."""
    import json
    import pathlib
    loc = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT"):
        d = json.loads((loc / f"{lang}.json").read_text(encoding="utf-8"))["translations"]
        for key in ("map_station_count_label", "map_station_count_hint",
                    "trip_prev", "trip_next", "trip_charge_view"):
            assert key in d, f"{lang} is missing {key}"


def test_the_map_form_posts_rather_than_gets():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "web" / "templates" / "map.html").read_text()
    form = src[src.index('<form', src.index("map_station_count_label") - 400):]
    assert 'method="post"' in form[:form.index(">") + 1]
