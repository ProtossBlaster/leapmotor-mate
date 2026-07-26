"""Wallbox 'calendar' Month view — same lean pattern as Ricariche/Viaggi: a month grid of
day totals computed from the ALREADY-STORED charge columns (ac_energy_kwh/energy_added_kwh),
not the per-session Home Assistant history fetch (_session_energy) the old accordion ran for
every session up front. That stays lazy, computed only for the one day a user opens.
Runs on a tmp_path DB (poller schema), CI-safe — no real Home Assistant involved."""
import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _seed_home_charge(pdb, cid, started, *, ended=None, ac=10.0, dc=9.0):
    """A HOME charge WITH a power curve (a charging=1 position inside its window) — the
    same "still has raw data" gate charges_with_power/_wallbox_home_charges_raw use."""
    ended = ended or started
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type)"
        " VALUES (?,1,?,?,40,60,?,?,'HOME')",
        (cid, started, ended, dc, ac))
    pdb._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, charging) VALUES (1,?,1)",
        (started,))
    pdb._conn.commit()


# ── get_wallbox_calendar_month: per-day AC/DC totals ──────────────────────────

def test_calendar_month_day_totals(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed_home_charge(pdb, 1, "2026-07-04T10:00:00+00:00", ac=10, dc=9)
    _seed_home_charge(pdb, 2, "2026-07-04T20:00:00+00:00", ac=20, dc=18)
    _seed_home_charge(pdb, 3, "2026-07-10T10:00:00+00:00", ac=5, dc=4.5)
    _seed_home_charge(pdb, 4, "2026-08-01T10:00:00+00:00", ac=99, dc=90)  # different month

    cal = db_reader.get_wallbox_calendar_month(2026, 7)
    assert cal["days"][4]["count"] == 2
    assert cal["days"][4]["ac"] == 30.0
    assert cal["days"][4]["dc"] == 27.0
    assert cal["days"][4]["eff"] == 90.0
    assert cal["total"]["count"] == 3
    assert cal["total"]["ac"] == 35.0


def test_calendar_month_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cal = db_reader.get_wallbox_calendar_month(2026, 7)
    assert cal["days"] == {}
    assert cal["total"]["count"] == 0
    assert cal["total"]["eff"] is None


def test_calendar_month_excludes_charges_without_a_power_curve(tmp_path, monkeypatch):
    """A HOME charge with no charging=1 position (data pruned, or never captured) has
    nothing to expand into a comparison chart, so it must not inflate the day total —
    same gate charges_with_power already applies for the old accordion."""
    pdb = _setup(tmp_path, monkeypatch)
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type)"
        " VALUES (1,1,'2026-07-04T10:00:00+00:00','2026-07-04T10:00:00+00:00',40,60,9,10,'HOME')")
    pdb._conn.commit()
    cal = db_reader.get_wallbox_calendar_month(2026, 7)
    assert cal["days"] == {}


def test_calendar_month_excludes_non_home_charges(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type) "
        "VALUES (1,1,'2026-07-04T10:00:00+00:00','2026-07-04T10:00:00+00:00',40,60,20,'HPC')")
    pdb._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, charging) VALUES (1,'2026-07-04T10:00:00+00:00',1)")
    pdb._conn.commit()
    cal = db_reader.get_wallbox_calendar_month(2026, 7)
    assert cal["days"] == {}


# ── get_wallbox_calendar_day ───────────────────────────────────────────────────

def test_calendar_day_sessions_most_recent_first(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed_home_charge(pdb, 1, "2026-07-04T10:00:00+00:00")
    _seed_home_charge(pdb, 2, "2026-07-04T20:00:00+00:00")
    _seed_home_charge(pdb, 3, "2026-07-10T10:00:00+00:00")   # different day
    sessions = db_reader.get_wallbox_calendar_day(2026, 7, 4)
    assert [s["id"] for s in sessions] == [2, 1]        # most recent (20:00 UTC) first
    assert sessions[0]["time"].count(":") == 1          # "HH:MM", local-tz (host-dependent, not asserted exactly)
    assert set(sessions[0].keys()) == {"id", "time"}    # no ac/dc/eff yet — main.py adds those lazily


def test_calendar_day_no_sessions_is_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert db_reader.get_wallbox_calendar_day(2026, 7, 4) == []


# ── get_wallbox_years ──────────────────────────────────────────────────────────

def test_wallbox_years_distinct_descending(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _seed_home_charge(pdb, 1, "2024-03-01T10:00:00+00:00")
    _seed_home_charge(pdb, 2, "2026-07-04T10:00:00+00:00")
    _seed_home_charge(pdb, 3, "2026-07-05T10:00:00+00:00")
    assert db_reader.get_wallbox_years() == [2026, 2024]


def test_wallbox_years_empty_when_no_sessions(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert db_reader.get_wallbox_years() == []
