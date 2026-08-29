"""The bundle carries the CHARGES and the TRIPS themselves, not only figures computed from them.

Silvio, 06/08/26, after #230 cost half an hour of log archaeology and still could not answer the
question: *«non potremmo esportare dal DB anche le ricariche e i viaggi così in caso di problemi su
un fronte o un altro li abbiamo direttamente dal db ma non tutto solo ad esempio gli ultimi 10-15
giorni»*.

Until now the bundle carried the poller's LOG (a text render of what the car said) and a handful of
derived summaries — vampire drain, SoC by day, the #109 cost line. It never carried the rows Mate
actually wrote. So every report of the shape "this trip is wrong" / "this charge is wrong" was
answered by reading 30 000 log lines and inferring what the table must contain.

⚠️ **The three choices, declared rather than made quietly:**

  · **15 days**, his number — but charges also keep a **floor of 10 rows**: someone who charges
    weekly has two in a fortnight, and two is not a triage.
  · **A cap of 200 trips, and it SAYS SO when it truncates.** A silently shortened list reads as
    "this is everything", which is how a wrong conclusion gets drawn from a right file.
  · **No positions.** Coordinates, geohashes, the charge's location name and URL, and the user's
    own note are all left out. The bundle's header promises "GPS removed" and that has to stay true
    — the note in particular is free text a user may have put an address in.

The `Last charges` line inside the cost section STAYS: it is the derived #109 diagnosis
(`show_wb`, `raw_ac`), not the raw record. Two lists, two questions — said out loud here so nobody
later "deduplicates" them into one.
"""
import os
import pathlib
import re

import db as PollerDB
import db_reader
import diagnostics
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Where the bundle looked BEFORE the fixture was isolated: the data dir conftest.py sets for the
# whole run (or, when DB_PATH is exported, the developer's real one). Captured at import, before
# any fixture can move it — it is the decoy's address in the guard below.
SUITE_DATA_DIR = pathlib.Path(os.environ.get("DB_PATH", "x")).parent

# Everything that must never reach the file. `note` is free text; `location_name`/`location_url`
# are user-typed and routinely hold a home address.
FORBIDDEN = ("latitude", "longitude", "start_lat", "start_lon", "end_lat", "end_lon",
             "start_geohash", "end_geohash", "location_name", "location_url", "note")


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = PollerDB.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    # 🔴 The ENVIRONMENT too, and not as a belt-and-braces: `diagnostics.data_dir()` reads
    # os.environ["DB_PATH"], which is a DIFFERENT thing from db_reader.DB_PATH and is set once,
    # for the whole run, in conftest.py. Patch only the module variable and the bundle's log
    # sections are read from a directory shared by every test in the suite — and, when DB_PATH is
    # already exported (conftest uses setdefault), from the developer's REAL Mate data directory,
    # logs and all. That is the shape of the intermittent red this file carried from 16/08 to
    # 29/08: never alone, never on a re-run, never reproducible, because it never depended on
    # this test at all. → test_the_bundle_reads_nothing_outside_this_test
    monkeypatch.setenv("DB_PATH", path)
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)

    def charge(day, kwh=30.0, cost=6.0, ac=None, hour=20):
        pdb._conn.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
            " energy_added_kwh, ac_energy_kwh, cost, charge_type, location_type, latitude,"
            " longitude, location_name, note) VALUES (1,?,?,40,80,?,?,?,'AC','HOME',45.1,9.2,"
            "'Casa mia, via Roma 1','targa AB123CD')",
            (f"2026-08-{day:02d}T{hour:02d}:00:00+00:00",
             f"2026-08-{day:02d}T{hour + 2:02d}:00:00+00:00", kwh, ac, cost))
        pdb._conn.commit()

    def trip(day, km=40.0, hour=8):
        pdb._conn.execute(
            "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
            " start_lat, start_lon, end_lat, end_lon, start_geohash, end_geohash, note)"
            " VALUES (1,?,?,?,80,60,45.1,9.2,45.4,9.6,'u0nd','u0ne','dal dentista')",
            (f"2026-08-{day:02d}T{hour:02d}:00:00+00:00",
             f"2026-08-{day:02d}T{hour + 1:02d}:00:00+00:00", km))
        pdb._conn.commit()

    return charge, trip


def _section(name):
    body = diagnostics.build_bundle("9.9.9")
    if f"----- {name}" not in body:
        return ""
    return body.split(f"----- {name}", 1)[1].split("\n-----", 1)[0]


# ── the rows are there ────────────────────────────────────────────────────────

def test_the_charges_are_in_the_file(car):
    charge, _ = car
    charge(3, kwh=31.5, cost=7.25)
    out = _section("charges")
    assert "2026-08-03" in out
    assert "31.5" in out and "7.25" in out


def test_the_trips_are_in_the_file(car):
    _, trip = car
    trip(4, km=42.7)
    out = _section("trips")
    assert "2026-08-04" in out
    assert "42.7" in out


# ── the window, and the floor under it ────────────────────────────────────────

def test_trips_stop_at_the_window(car):
    """15 days, counted from the newest row rather than from today — a bundle downloaded weeks
    after the car went quiet must still carry the last fortnight it drove."""
    _, trip = car
    trip(20, km=11.0)
    trip(1, km=22.0)               # 19 days earlier
    out = _section("trips")
    assert "11.0" in out
    assert "22.0" not in out, "a trip outside the window came along"


def test_charges_keep_a_floor_of_ten(car):
    """Someone who charges weekly has two in a fortnight. The window must not be the only rule."""
    charge, _ = car
    for i, day in enumerate(range(1, 13)):     # a charge a day, the oldest 30+ days back
        charge(day, kwh=10.0 + i, cost=1.0 + i, hour=6)
    out = _section("charges")
    assert len([ln for ln in out.splitlines() if re.match(r"\s+2026-", ln)]) >= 10


# ── the cap says it capped ────────────────────────────────────────────────────

def test_a_truncated_list_says_so(car):
    """🔴 No silent caps. A shortened list that looks complete is how a right file produces a
    wrong conclusion."""
    import db as D
    d = D.Database(db_reader.DB_PATH)
    for i in range(210):
        d._conn.execute(
            "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
            " VALUES (1,?,?,5.0,80,79)",
            (f"2026-08-20T{i // 60:02d}:{i % 60:02d}:00+00:00",
             f"2026-08-20T{i // 60:02d}:{i % 60:02d}:30+00:00"))
    d._conn.commit()
    out = _section("trips")
    assert "200" in out and re.search(r"(?i)more|older|truncat|of \d+", out), \
        "the list was shortened without saying so"


# ── and nothing that locates anybody ──────────────────────────────────────────

def test_no_coordinates_and_no_free_text(car):
    """The header promises GPS removed. `note` and `location_name` are user-typed and routinely
    hold an address or a plate."""
    charge, trip = car
    charge(3)
    trip(3)
    body = diagnostics.build_bundle("9.9.9")
    for needle in ("45.1", "9.2", "45.4", "u0nd", "Casa mia", "via Roma", "targa AB123CD",
                   "dal dentista"):
        # On failure, print WHERE it was found. This assertion has gone red twice in a full run
        # (16/08) and never once on its own or on a re-run, with the file order fixed —
        # pytest-randomly is not installed — so the cause is not the sequence and is still unknown.
        # A bare "leaked '45.1'" says nothing: several of these needles are short enough to appear
        # inside an unrelated number, and the next occurrence has to be answerable from its output
        # rather than reproducible on demand.
        if needle in body:
            i = body.index(needle)
            raise AssertionError(
                f"the bundle leaked {needle!r} at offset {i}:\n"
                f"  …{body[max(0, i - 90):i + 60]}…")


def test_the_bundle_reads_nothing_outside_this_test(car, tmp_path, monkeypatch):
    """The bundle must be built from THIS test's data dir and nothing else.

    This is the one that would have caught it. From 16/08 the privacy assertion above went red
    about one full run in four, never alone and never on a re-run, and the cause was never in the
    bundle: `diagnostics.data_dir()` resolves os.environ["DB_PATH"], the fixture only replaced
    `db_reader.DB_PATH`, and the two are different values. So the log sections came from the
    suite-wide directory conftest.py creates — or, when DB_PATH is exported before the run, from a
    real Mate data directory, whose poller log is full of coordinates.

    Measured, not argued: a decoy log dropped in the shared directory put all three needles
    ('45.4', '9.2', 'dal dentista') into the bundle of an otherwise isolated test.
    """
    charge, _trip = car
    charge(3)
    # A log where the bundle used to look, holding exactly what must never travel.
    decoy_dirs = [d for d in {SUITE_DATA_DIR, tmp_path.parent} if d.is_dir()]
    assert decoy_dirs, "no directory to plant the decoy in — the guard would prove nothing"
    for d in decoy_dirs:
        (d / "mate-poller.log").write_text(
            "2026-08-16 12:00:00 INFO position lat=45.4085 lon=9.2272\n"
            "2026-08-16 12:00:30 INFO nota: 'dal dentista'\n", encoding="utf-8")
    try:
        body = diagnostics.build_bundle("9.9.9")
        for needle in ("45.4", "9.2", "dal dentista"):
            assert needle not in body, (
                f"the bundle picked up {needle!r} from a log this test never wrote — it is reading "
                f"a directory it does not own")
    finally:
        for d in decoy_dirs:
            (d / "mate-poller.log").unlink(missing_ok=True)


def test_the_column_names_alone_are_not_a_leak():
    """Anchored on the SQL, not on the output: a SELECT that reads a forbidden column can still
    print something derived from it later."""
    src = (ROOT / "web" / "diagnostics.py").read_text()
    for fn in ("_charges_section", "_trips_section"):
        body = src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
        # ⚠️ The DOCSTRING is stripped first: it names every forbidden column on purpose, to say
        # they are excluded. The loose version of this test read that sentence and called the code
        # a leak — a test failing on the comment that documents the very rule it checks.
        code = body.split('"""', 2)[2] if body.count('"""') >= 2 else body
        for col in FORBIDDEN:
            assert not re.search(rf"\b{col}\b", code), f"{fn} reads {col}"


# ── the #109 line is NOT the same list ────────────────────────────────────────

def test_an_overnight_charge_says_it_crossed_midnight(car):
    """Most home charges are overnight, and a bare `23:50 → 06:12` reads as a session that went
    backwards in time. Three characters, one less double-take."""
    charge, _ = car
    charge(3, hour=22)                      # 22:00 → 24:00 the same day
    import db as D
    d = D.Database(db_reader.DB_PATH)
    d._conn.execute("UPDATE charges SET ended_at = '2026-08-04T06:12:00+00:00'")
    d._conn.commit()
    assert "+1d 06:12" in _section("charges")


def test_a_charge_still_running_is_named_not_blanked(car):
    """A blank end is ambiguous — lost row, or still arriving? It has to say which."""
    import db as D
    d = D.Database(db_reader.DB_PATH)
    d._conn.execute("INSERT INTO charges (vehicle_id, started_at, start_soc, energy_added_kwh)"
                    " VALUES (1,'2026-08-06T12:34:00+00:00',66.1,4.0)")
    d._conn.commit()
    assert "IN CORSO" in _section("charges")


def test_the_cost_diagnosis_line_survives(car):
    """It answers a different question — whether the CARD would show the AC figure — and it is
    derived, not raw. Kept deliberately; this test is here so nobody merges the two by accident."""
    charge, _ = car
    charge(3, ac=28.0)
    body = diagnostics.build_bundle("9.9.9")
    assert "show_wb=" in body and "raw_ac=" in body
