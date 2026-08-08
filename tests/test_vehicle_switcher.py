"""The sidebar car switcher: which car every scoped read resolves to.

One setting (`active_vehicle_vin`) moves trips, charges, map, stats, the model badge and the
ability-gated nav together, because they all resolve through `_current_vehicle_id()`. The
properties that matter are the ones that keep a single-car install — i.e. everyone, today —
byte-identical, and that stop a bad or stale choice from stranding the UI on a car that isn't
there.
"""
import db as D
import db_reader


def _db(tmp_path, monkeypatch, *cars):
    path = str(tmp_path / "sw.db")
    db = D.Database(path)
    for i, (vin, car_type) in enumerate(cars, start=1):
        db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (?,?,?)",
                         (i, vin, car_type))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db


def test_defaults_to_the_first_car_when_nothing_is_chosen(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    assert db_reader._current_vehicle_id() == 1


def test_the_choice_moves_every_scoped_read(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    assert db_reader.set_active_vehicle("VIN_TWO") is True
    assert db_reader._current_vehicle_id() == 2
    v, _ = db_reader.get_vehicle()
    assert v["car_type"] == "T03"          # the model badge + per-model gating follow it


def test_a_stale_choice_falls_back_instead_of_stranding_the_ui(tmp_path, monkeypatch):
    """The named car was removed (re-setup, account change). Nothing should blank: the ORDER BY
    scores every row equally and the tiebreak on id hands back the first car, as before."""
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "VIN_LONG_GONE")
    assert db_reader._current_vehicle_id() == 1
    v, _ = db_reader.get_vehicle()
    assert v is not None and v["id"] == 1


def test_an_unknown_vin_is_refused_rather_than_stored(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    assert db_reader.set_active_vehicle("NOT_A_CAR") is False
    assert db_reader.get_setting(db_reader.ACTIVE_VEHICLE_SETTING, "") == ""
    assert db_reader._current_vehicle_id() == 1


def test_get_vehicles_lists_them_oldest_first(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    assert [v["car_type"] for v in db_reader.get_vehicles()] == ["B10", "T03"]


def test_single_car_is_untouched(tmp_path, monkeypatch):
    """One car → the switcher isn't rendered (base.html gates on len > 1) and the resolution is
    the same id with or without a choice stored. No install in the field can notice this feature."""
    _db(tmp_path, monkeypatch, ("VIN_ONLY", "B10"))
    assert len(db_reader.get_vehicles()) == 1
    assert db_reader._current_vehicle_id() == 1
    db_reader.set_active_vehicle("VIN_ONLY")
    assert db_reader._current_vehicle_id() == 1


def test_no_vehicle_yet_still_resolves_to_none(tmp_path, monkeypatch):
    """Fresh install: None keeps `COALESCE(?, vehicle_id)` matching every row rather than
    filtering the UI to empty."""
    _db(tmp_path, monkeypatch)
    assert db_reader._current_vehicle_id() is None
    assert db_reader.get_vehicles() == []


# ── what the recovered tests did not cover ────────────────────────────────────

def _row(db, table, vehicle_id, **cols):
    keys = ", ".join(["vehicle_id"] + list(cols))
    marks = ", ".join(["?"] * (1 + len(cols)))
    db._conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({marks})",
                     (vehicle_id, *cols.values()))
    db._conn.commit()


def test_the_reads_really_follow_the_choice(tmp_path, monkeypatch):
    """🔑 The one that matters. `_current_vehicle_id()` returning the right id proves nothing on
    its own — what proves it is a page read handing back the other car's rows and not this one's.
    The second car deliberately carries MORE and MORE RECENT data, so a forgotten scope shows up
    as the wrong answer rather than as an empty one."""
    db = _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    _row(db, "trips", 1, started_at="2026-07-01T08:00:00+00:00",
         ended_at="2026-07-01T09:00:00+00:00", distance_km=100.0, start_soc=80, end_soc=60)
    for d in (2, 3, 4):
        _row(db, "trips", 2, started_at=f"2026-07-0{d}T08:00:00+00:00",
             ended_at=f"2026-07-0{d}T09:00:00+00:00", distance_km=500.0, start_soc=80, end_soc=60)
    assert db_reader.get_stats_summary()["total_km"] == 100.0, "car one, though car two has more"
    db_reader.set_active_vehicle("VIN_TWO")
    assert db_reader.get_stats_summary()["total_km"] == 1500.0
    assert db_reader.get_stats_summary()["trip_count"] == 3


def test_the_latest_status_is_the_chosen_cars(tmp_path, monkeypatch):
    """The Overview's hero. The other car's position is NEWER — the one thing that makes an
    unscoped `ORDER BY id DESC LIMIT 1` hand back the wrong car."""
    db = _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    _row(db, "positions", 1, recorded_at="2026-07-01T08:00:00+00:00", soc=41.0, odometer_km=1000.0)
    _row(db, "positions", 2, recorded_at="2026-07-09T08:00:00+00:00", soc=92.0, odometer_km=9000.0)
    assert db_reader.get_latest_status()["soc"] == 41.0
    db_reader.set_active_vehicle("VIN_TWO")
    assert db_reader.get_latest_status()["soc"] == 92.0


def test_the_badge_becomes_a_picker_only_from_the_second_car():
    """One car has to render byte-identically to what every install shows today — that is what
    makes this shippable before anybody has two cars."""
    import pathlib
    base = (pathlib.Path(__file__).parents[1] / "web" / "templates" / "base.html").read_text()
    assert "{% if vehicles and vehicles|length > 1 %}" in base
    assert 'hx-post="api/select-vehicle"' in base
    assert '<span class="text-xs text-slate-400">{{ vehicle.car_type }}' in base, \
        "the plain badge is still there for the single-car case"


def test_the_route_exists_and_refuses_an_unknown_vin():
    import pathlib
    main = (pathlib.Path(__file__).parents[1] / "web" / "main.py").read_text()
    assert '@app.post("/api/select-vehicle")' in main
    body = main.split('@app.post("/api/select-vehicle")', 1)[1].split("\n@app.", 1)[0]
    assert "if not db_reader.set_active_vehicle" in body and "status_code=204" in body
    assert '"HX-Refresh": "true"' in body, "the choice moves every panel, so the page reloads"
    assert '"vehicles": _vehicles' in main, "the list has to reach base.html through _ctx"


def test_every_language_can_label_the_picker():
    import json
    import pathlib
    root = pathlib.Path(__file__).parents[1] / "web" / "locales"
    for f in sorted(root.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))["translations"]
        assert d.get("switch_vehicle"), f"{f.name} cannot label the car picker"


# ── the per-model gating rides on the same setting ────────────────────────────

def test_the_model_gated_pages_follow_the_chosen_car(tmp_path, monkeypatch):
    """With a B10 and a T03 on one account the interface must not offer the wrong model's
    features. It follows for free — the gating hangs off `get_vehicle()`, which resolves through
    `_current_vehicle_id()` like everything else — but "for free" is a claim, so it is measured
    here and on a real container: the B10 shows *Prepare car* and the T03 shows *Navigation*
    instead, and the two swap the moment the picker moves."""
    import capability_profile
    _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))

    def shown(page):
        v, _ = db_reader.get_vehicle()
        return not capability_profile.model_hidden((v or {}).get("car_type"), page)

    assert shown("prepare_car") is True, "the B10 prepares"
    db_reader.set_active_vehicle("VIN_TWO")
    assert shown("prepare_car") is False, "the T03 does not — and the menu has to follow"
    db_reader.set_active_vehicle("VIN_ONE")
    assert shown("prepare_car") is True, "and back again"


def test_the_battery_capacity_is_the_chosen_cars(tmp_path, monkeypatch):
    """🔑 The dangerous one, and the reason this was already fixed per-car in v2.2.0: capacity is
    what trip and charge energies are COMPUTED from. A B10's 65 kWh applied to a T03's 36 inflates
    every figure by 80%, in the rows themselves, permanently."""
    db = _db(tmp_path, monkeypatch, ("VIN_ONE", "B10"), ("VIN_TWO", "T03"))
    db._conn.execute("UPDATE vehicles SET capacity_kwh = 65.0 WHERE vin='VIN_ONE'")
    db._conn.execute("UPDATE vehicles SET capacity_kwh = 36.0 WHERE vin='VIN_TWO'")
    db._conn.commit()
    assert db_reader.get_battery_capacity_kwh() == 65.0
    db_reader.set_active_vehicle("VIN_TWO")
    assert db_reader.get_battery_capacity_kwh() == 36.0


# ── the range-extender flag is a fact about a CAR ─────────────────────────────

def test_a_range_extender_does_not_make_the_other_car_one(tmp_path, monkeypatch):
    """🔴 It was one flag for the install, written the first time ANY car reported a fuel tank.
    A range-extender and a plain electric car on the same account would have put the REEV pages on
    both — and on the official build, which withholds battery-derived figures where a generator
    makes them meaningless, it would have withheld them from the very car they are correct for."""
    _db(tmp_path, monkeypatch, ("VIN_BEV", "B10"), ("VIN_REEV", "C10"))
    db_reader.set_setting("is_reev_vin_reev", "1")
    db_reader.set_setting("is_reev_vin_bev", "0")
    db_reader.set_setting("is_reev", "1")          # the account flag, written by the older poller
    assert db_reader.is_reev_car() is False, "the selected car is the BEV"
    db_reader.set_active_vehicle("VIN_REEV")
    assert db_reader.is_reev_car() is True


def test_a_car_the_poller_has_not_reached_keeps_the_old_answer(tmp_path, monkeypatch):
    """⚠️ Absence is not "no". A car with no per-car key yet — a half-updated install, the poller
    not having come round — falls back to the account flag, which is exactly what it read before.
    Reading absence as BEV would hide a real range-extender's pages on update."""
    _db(tmp_path, monkeypatch, ("VIN_OLD", "C10"))
    db_reader.set_setting("is_reev", "1")
    assert db_reader.is_reev_car() is True
    db_reader.set_setting("is_reev_vin_old", "0")  # …and the poller's own answer then wins
    assert db_reader.is_reev_car() is False


def test_no_page_reads_the_account_flag_directly():
    """Eleven call sites went through one function so this could be asserted in one place. A new
    one spelled the old way would gate on the install instead of on the car — silently, and only
    for the two people who have two cars. → [[feedback-gate-a-feature-find-every-copy]]"""
    import pathlib
    root = pathlib.Path(__file__).parents[1] / "web"
    offenders = []
    for f in (root / "main.py", root / "db_reader.py"):
        for i, line in enumerate(f.read_text().splitlines(), start=1):
            if 'get_setting("is_reev"' in line and "is_reev_car" not in line:
                offenders.append(f"{f.name}:{i}")
    # exactly one: the fallback inside is_reev_car itself
    assert len(offenders) == 1, f"the account flag is read directly at {offenders}"
