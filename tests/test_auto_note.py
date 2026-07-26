"""The 🧭 auto-generated address/time/temperature summary, for both trips and charges.
Simplified design: no separate read-only field — the summary is written STRAIGHT INTO
the note (the one note field). For a brand-new trip/charge the poller populates it
automatically (only_if_note_empty=True — see tests/test_auto_note_recorder.py); the 🧭
button on an existing record always overwrites, but the UI warns first with a confirm
popup when there's manual text to lose (trip_detail.html / charge_card.html's conditional
hx-confirm). Live network calls (reverse-geocoding has no caching and Nominatim's usage
policy forbids bulk lookups) are safe here because each call is for exactly one NEW
trip/charge, never a historical backfill sweep.
"""
import asyncio

import pytest

import db as D
import db_reader


def _hhmm(iso: str) -> str:
    """Expected local HH:MM for a UTC timestamp — the note formats in local time
    (db_reader._local_dt), so a literal UTC hour would be wrong under any non-UTC tz."""
    return db_reader._local_dt(iso).strftime("%H:%M")


class _Req:
    """The endpoints only ever pass this to the template renderer, which is faked below."""


class _FakeTemplates:
    def __init__(self):
        self.rendered = []

    def TemplateResponse(self, request, name, ctx):   # noqa: N802 — mirrors Starlette's name
        self.rendered.append((name, ctx))
        return ctx


@pytest.fixture
def pdb(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return database


# ── get_position_near ──────────────────────────────────────────────────────────

def _add_position(pdb, ts, outside_temp=None, battery_min_temp=None):
    pdb._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, outside_temp, battery_min_temp) "
        "VALUES (1,?,?,?)", (ts, outside_temp, battery_min_temp))
    pdb._conn.commit()


def test_get_position_near_picks_closest_within_tolerance(pdb):
    _add_position(pdb, "2026-07-04T09:50:00+00:00", outside_temp=18)
    _add_position(pdb, "2026-07-04T10:05:00+00:00", outside_temp=20, battery_min_temp=24)
    _add_position(pdb, "2026-07-04T10:40:00+00:00", outside_temp=30)

    pos = db_reader.get_position_near("2026-07-04T10:00:00+00:00")
    assert pos["outside_temp"] == 20
    assert pos["battery_min_temp"] == 24


def test_get_position_near_none_outside_tolerance(pdb):
    _add_position(pdb, "2026-07-04T09:00:00+00:00", outside_temp=18)
    assert db_reader.get_position_near("2026-07-04T10:00:00+00:00", tolerance_min=20) is None


def test_get_position_near_none_when_ts_missing_or_unparseable(pdb):
    assert db_reader.get_position_near(None) is None
    assert db_reader.get_position_near("not-a-date") is None


# ── generate_trip_auto_note ─────────────────────────────────────────────────────

def _add_trip(pdb, started, ended, start_lat=45.0, start_lon=9.0, end_lat=45.1, end_lon=9.1,
              outside_temp_start_c=None, outside_temp_end_c=None, note=None):
    pdb._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon,"
        " outside_temp_start_c, outside_temp_end_c, note) VALUES (1,?,?,?,?,?,?,?,?,?)",
        (started, ended, start_lat, start_lon, end_lat, end_lon,
         outside_temp_start_c, outside_temp_end_c, note))
    pdb._conn.commit()
    return db_reader._get().execute("SELECT MAX(id) AS id FROM trips").fetchone()["id"]


def test_generate_trip_auto_note_builds_and_persists_into_note(pdb, monkeypatch):
    import geocode
    tid = _add_trip(pdb, "2026-07-04T10:00:00+00:00", "2026-07-04T10:30:00+00:00",
                    outside_temp_start_c=18.0, outside_temp_end_c=21.0)
    addrs = {(45.0, 9.0): "Via Roma 1, Milano", (45.1, 9.1): "Via Torino 2, Milano"}
    monkeypatch.setattr(geocode, "reverse_geocode",
                        lambda lat, lon, provider, api_key: addrs[(lat, lon)])

    text = db_reader.generate_trip_auto_note(tid)

    assert "Via Roma 1, Milano" in text
    assert "Via Torino 2, Milano" in text
    assert _hhmm("2026-07-04T10:00:00+00:00") in text and _hhmm("2026-07-04T10:30:00+00:00") in text
    assert "18" in text and "21" in text
    row = db_reader._get().execute("SELECT note FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["note"] == text


def test_generate_trip_auto_note_missing_trip_returns_none(pdb):
    assert db_reader.generate_trip_auto_note(999) is None


def test_generate_trip_auto_note_survives_a_geocoding_failure(pdb, monkeypatch):
    """One endpoint's network hiccup must not blank the other endpoint's time+temperature —
    reverse_geocode's own urllib call can raise (timeout, DNS blip), not just return None."""
    import geocode
    tid = _add_trip(pdb, "2026-07-04T10:00:00+00:00", "2026-07-04T10:30:00+00:00",
                    outside_temp_start_c=18.0, outside_temp_end_c=21.0)
    monkeypatch.setattr(geocode, "reverse_geocode",
                        lambda lat, lon, provider, api_key: (_ for _ in ()).throw(TimeoutError()))

    text = db_reader.generate_trip_auto_note(tid)

    assert _hhmm("2026-07-04T10:00:00+00:00") in text and _hhmm("2026-07-04T10:30:00+00:00") in text
    assert "18" in text and "21" in text


def test_generate_trip_auto_note_regenerating_overwrites(pdb, monkeypatch):
    import geocode
    tid = _add_trip(pdb, "2026-07-04T10:00:00+00:00", "2026-07-04T10:30:00+00:00")
    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, provider, api_key: "First")
    first = db_reader.generate_trip_auto_note(tid)
    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, provider, api_key: "Second")
    second = db_reader.generate_trip_auto_note(tid)

    assert "First" in first and "Second" in second
    row = db_reader._get().execute("SELECT note FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["note"] == second


def test_generate_trip_auto_note_default_overwrites_existing_note(pdb, monkeypatch):
    """The manual 🧭 button always overwrites — the UI is what confirms with the user
    first, not the generator (which stays a dumb, always-overwrite primitive)."""
    import geocode
    tid = _add_trip(pdb, "2026-07-04T10:00:00+00:00", "2026-07-04T10:30:00+00:00",
                    note="Traffico intenso in autostrada")
    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, provider, api_key: "New address")

    text = db_reader.generate_trip_auto_note(tid)

    assert "Traffico intenso" not in text
    row = db_reader._get().execute("SELECT note FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["note"] == text


def test_generate_trip_auto_note_only_if_empty_skips_existing_note(pdb, monkeypatch):
    """The automatic at-trip-close path (poller) must never clobber a note that already
    has manual text in it."""
    import geocode
    tid = _add_trip(pdb, "2026-07-04T10:00:00+00:00", "2026-07-04T10:30:00+00:00",
                    note="Traffico intenso in autostrada")
    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, provider, api_key: "New address")

    result = db_reader.generate_trip_auto_note(tid, only_if_note_empty=True)

    assert result == "Traffico intenso in autostrada"
    row = db_reader._get().execute("SELECT note FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["note"] == "Traffico intenso in autostrada"


def test_generate_trip_auto_note_only_if_empty_fills_a_blank_note(pdb, monkeypatch):
    import geocode
    tid = _add_trip(pdb, "2026-07-04T10:00:00+00:00", "2026-07-04T10:30:00+00:00")
    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, provider, api_key: "Fresh address")

    text = db_reader.generate_trip_auto_note(tid, only_if_note_empty=True)

    assert "Fresh address" in text
    row = db_reader._get().execute("SELECT note FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["note"] == text


# ── generate_charge_auto_note ────────────────────────────────────────────────────

def _add_charge(pdb, started, ended, lat=45.0, lon=9.0, location_type="FAST", location_name=None,
                note=None):
    pdb._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, latitude, longitude,"
        " location_type, location_name, note) VALUES (1,?,?,?,?,?,?,?)",
        (started, ended, lat, lon, location_type, location_name, note))
    pdb._conn.commit()
    return db_reader._get().execute("SELECT MAX(id) AS id FROM charges").fetchone()["id"]


def test_generate_charge_auto_note_builds_and_persists_into_note(pdb, monkeypatch):
    import charger_locator
    cid = _add_charge(pdb, "2026-07-04T12:00:00+00:00", "2026-07-04T12:30:00+00:00",
                      location_name="Ionity Binasco")
    _add_position(pdb, "2026-07-04T12:00:00+00:00", outside_temp=19, battery_min_temp=24)
    _add_position(pdb, "2026-07-04T12:30:00+00:00", outside_temp=20, battery_min_temp=26)
    monkeypatch.setattr(charger_locator, "find_station_candidates",
                        lambda la, lo: ([{"name": "Ionity Binasco", "address": "Via Milano 1"}], True))

    text = db_reader.generate_charge_auto_note(cid)

    assert "Via Milano 1" in text
    assert _hhmm("2026-07-04T12:00:00+00:00") in text and _hhmm("2026-07-04T12:30:00+00:00") in text
    assert "24" in text and "26" in text
    row = db_reader._get().execute("SELECT note FROM charges WHERE id=?", (cid,)).fetchone()
    assert row["note"] == text


def test_generate_charge_auto_note_skips_address_lookup_for_home(pdb, monkeypatch):
    import charger_locator
    called = []
    monkeypatch.setattr(charger_locator, "find_station_candidates",
                        lambda la, lo: called.append(1) or ([], True))
    cid = _add_charge(pdb, "2026-07-04T12:00:00+00:00", "2026-07-04T12:30:00+00:00",
                      location_type="HOME")

    db_reader.generate_charge_auto_note(cid)

    assert not called   # HOME never triggers a station lookup — no site to address


def test_generate_charge_auto_note_missing_charge_returns_none(pdb):
    assert db_reader.generate_charge_auto_note(999) is None


def test_generate_charge_auto_note_blank_temps_when_no_telemetry(pdb, monkeypatch):
    """Some cars don't report outside_temp at all (known sensor gap) — the note still
    carries the address+time, just without the missing figure."""
    import charger_locator
    monkeypatch.setattr(charger_locator, "find_station_candidates",
                        lambda la, lo: ([{"name": None, "address": "Via Milano 1"}], True))
    cid = _add_charge(pdb, "2026-07-04T12:00:00+00:00", "2026-07-04T12:30:00+00:00")

    text = db_reader.generate_charge_auto_note(cid)

    assert "Via Milano 1" in text
    assert _hhmm("2026-07-04T12:00:00+00:00") in text and _hhmm("2026-07-04T12:30:00+00:00") in text


def test_generate_charge_auto_note_borrows_address_from_a_sibling_option(pdb, monkeypatch):
    """Real-world bug: the charge's own name-matched option (e.g. the OSM node) can have
    no street address even though a DIFFERENT source at the SAME physical site (e.g. the
    OCM/PUN entry a few metres away, under a different name) does. charger_locator keeps
    them as separate options on purpose (lets the manual relocate button offer a choice),
    so the address must be borrowed here rather than silently left blank."""
    import charger_locator
    monkeypatch.setattr(charger_locator, "find_station_candidates", lambda la, lo: ([
        {"name": "IONITY Versilia Ovest", "address": None, "dist_m": 9},
        {"name": "Ionity AdS Versilia Ovest", "address": "A12 km 131, Pietrasanta", "dist_m": 16},
    ], True))
    cid = _add_charge(pdb, "2026-07-04T12:00:00+00:00", "2026-07-04T12:30:00+00:00",
                      location_name="IONITY Versilia Ovest")

    text = db_reader.generate_charge_auto_note(cid)

    assert "A12 km 131, Pietrasanta" in text


def test_generate_charge_auto_note_only_if_empty_skips_existing_note(pdb, monkeypatch):
    import charger_locator
    monkeypatch.setattr(charger_locator, "find_station_candidates",
                        lambda la, lo: ([{"name": None, "address": "Via Milano 1"}], True))
    cid = _add_charge(pdb, "2026-07-04T12:00:00+00:00", "2026-07-04T12:30:00+00:00",
                      note="Colonnina lenta ma comoda")

    result = db_reader.generate_charge_auto_note(cid, only_if_note_empty=True)

    assert result == "Colonnina lenta ma comoda"
    row = db_reader._get().execute("SELECT note FROM charges WHERE id=?", (cid,)).fetchone()
    assert row["note"] == "Colonnina lenta ma comoda"


# ── main.py endpoints ────────────────────────────────────────────────────────────

@pytest.fixture
def main_env(pdb, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    fake = _FakeTemplates()
    monkeypatch.setattr(main, "templates", fake)
    return main, fake


def test_trip_auto_note_endpoint_writes_note_and_renders_textarea(main_env, monkeypatch):
    import geocode
    main, fake = main_env
    conn = db_reader._conn_rw()
    conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon)"
        " VALUES (1,'2026-07-04T10:00:00+00:00','2026-07-04T10:30:00+00:00',45.0,9.0,45.1,9.1)")
    conn.commit()
    tid = conn.execute("SELECT MAX(id) AS id FROM trips").fetchone()["id"]
    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, provider, api_key: "Some Address")

    asyncio.run(main.trip_generate_auto_note(_Req(), tid))

    name, ctx = fake.rendered[-1]
    assert name == "partials/trip_note_textarea.html"
    assert "Some Address" in ctx["trip"]["note"]
    row = db_reader._get().execute("SELECT note FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["note"] == ctx["trip"]["note"]


def test_charge_auto_note_endpoint_writes_note_and_renders_textarea(main_env, monkeypatch):
    import charger_locator
    main, fake = main_env
    conn = db_reader._conn_rw()
    conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, latitude, longitude, location_type)"
        " VALUES (1,'2026-07-04T12:00:00+00:00','2026-07-04T12:30:00+00:00',45.0,9.0,'FAST')")
    conn.commit()
    cid = conn.execute("SELECT MAX(id) AS id FROM charges").fetchone()["id"]
    monkeypatch.setattr(charger_locator, "find_station_candidates",
                        lambda la, lo: ([{"name": None, "address": "Via Milano 1"}], True))

    asyncio.run(main.charge_generate_auto_note(_Req(), cid))

    name, ctx = fake.rendered[-1]
    assert name == "partials/charge_note_textarea.html"
    assert "Via Milano 1" in ctx["c"]["note"]


# ── i18n coverage ────────────────────────────────────────────────────────────────

def test_auto_note_strings_present_in_every_locale():
    import i18n
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT"):
        t = i18n.get_t(lang)
        for key in ("auto_note_generate_btn", "auto_note_overwrite_confirm"):
            assert t(key) != key, f"{lang} is missing {key}"
