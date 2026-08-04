"""The web must not fall over on a database the poller has not migrated yet.

v3.6.6 added `charges.gross_kwh` (#222). The migration lives in the POLLER; the web layer serves the
same file and never alters it. So there is a window — after an update, before the poller's next
start — and a permanent state for any install whose poller has not run, where the column is simply
absent. Every query that NAMES it raises `sqlite3.OperationalError`, and that is a 500 on the page,
not a missing figure.

Found on Silvio's own instance hours after the release: `gross_kwh in the DB: False`,
`get_charge_stats() → OperationalError`. Three read paths were exposed and a fourth degraded in
silence.

The general shape, which is what these tests hold: **a reader may not assume a writer's migration
has run.** Adding a column to the poller's schema is not enough to make it safe to name in the web.
"""
import pathlib
import sqlite3

import db_reader
import pytest


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A charges table as it stood BEFORE the #222 migration — no gross_kwh, everything else there."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE vehicles (id INTEGER PRIMARY KEY, vin TEXT, car_type TEXT)")
    conn.execute("INSERT INTO vehicles VALUES (1, 'V', 'B10')")
    conn.execute("""CREATE TABLE charges (
        id INTEGER PRIMARY KEY, vehicle_id INT, started_at TEXT, ended_at TEXT,
        start_soc REAL, end_soc REAL, energy_added_kwh REAL, ac_energy_kwh REAL,
        location_type TEXT, cost REAL, is_free INT, charge_type TEXT, max_power_kw REAL,
        duration_min REAL, latitude REAL, longitude REAL, location_name TEXT,
        location_url TEXT, note TEXT)""")
    conn.execute("""CREATE TABLE trips (
        id INTEGER PRIMARY KEY, vehicle_id INT, started_at TEXT, ended_at TEXT,
        distance_km REAL, start_soc REAL, end_soc REAL, merged_into_id INT)""")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
                 " energy_added_kwh, ac_energy_kwh, location_type, cost, charge_type, max_power_kw,"
                 " duration_min) VALUES (1,1,'2026-07-03T08:00:00+00:00','2026-07-03T10:00:00+00:00',"
                 "20,70,30.0,NULL,'AC',9.0,'AC',7.4,120)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def test_the_column_is_reported_absent(old_db):
    assert db_reader._charges_have_gross(db_reader._get()) is False


def test_the_charges_page_figures_still_come_out(old_db):
    """This is the one that was a 500. The Charges page cannot render without it."""
    s = db_reader.get_charge_stats()
    assert s["total_kwh"] == 30.0 and s["session_count"] == 1


def test_the_ac_dc_split_still_comes_out(old_db):
    split = db_reader.get_ac_dc_stats()
    assert split["ac"]["kwh"] == 30.0 and split["total"] == 1


def test_the_range_extender_card_does_not_vanish(old_db):
    """It was wrapped in a try/except, so it did not crash — it returned nothing, which is worse in
    its own way: a card that disappears reads as "you have no data"."""
    out = db_reader.reev_actual_spend()
    assert out is None or out.get("kwh") == 30.0


def test_a_trip_detail_can_still_find_its_charges(old_db):
    """_trip_stop_charges names the column too, and its page has no guard around it."""
    db = db_reader._get()
    assert db_reader._trip_stop_charges(db, 1, "2026-07-03T07:00:00+00:00") == []


def test_the_answers_match_a_migrated_database(tmp_path, monkeypatch, old_db):
    """Degrading is only acceptable if it degrades to the RIGHT answer. Without the column there are
    no typed figures anyway, so both databases must report the same thing."""
    before = db_reader.get_charge_stats()
    conn = sqlite3.connect(old_db)
    conn.execute("ALTER TABLE charges ADD COLUMN gross_kwh REAL")
    conn.commit()
    conn.close()
    assert db_reader._charges_have_gross(db_reader._get()) is True
    assert db_reader.get_charge_stats()["total_kwh"] == before["total_kwh"]


def test_the_column_appearing_mid_flight_is_noticed(old_db):
    """The poller can migrate while the web is running. A cached "no" would keep the page on the old
    answer until someone restarted it, which is exactly the kind of thing nobody would connect."""
    db = db_reader._get()
    assert db_reader._charges_have_gross(db) is False
    conn = sqlite3.connect(old_db)
    conn.execute("ALTER TABLE charges ADD COLUMN gross_kwh REAL")
    conn.commit()
    conn.close()
    assert db_reader._charges_have_gross(db_reader._get()) is True


def test_no_read_path_names_the_column_without_asking_first():
    """The rule, held directly: every hand-written query that mentions gross_kwh must be guarded by
    the schema check (or by a try/except, as reev_actual_spend already was)."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "db_reader.py").read_text()
    lines = src.split("\n")
    unguarded = []
    for i, line in enumerate(lines):
        if "gross_kwh" not in line or "SELECT" not in line.upper() and "gross_kwh," not in line:
            continue
        j = i
        while j > 0 and not lines[j].startswith("def "):
            j -= 1
        body = "\n".join(lines[j:i + 30])
        if "_charges_have_gross" in body or "sqlite3.Error" in body:
            continue
        unguarded.append(lines[j].split("(")[0].replace("def ", ""))
    assert not unguarded, f"these name the column with no guard: {unguarded}"


# ── and the class, not just this column ───────────────────────────────────────

def test_the_web_brings_the_schema_up_itself():
    """The real answer: a reader that depends on a schema should guarantee it, not hope. Guarding
    one column fixes one column — this is what stops the next one."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()
    assert "def _ensure_schema()" in src
    assert "\n_ensure_schema()" in src, "defined but never called"
    body = src.split("def _ensure_schema()", 1)[1].split("\n_ensure_schema()", 1)[0]
    assert "ensure_schema(conn)" in body


def test_it_borrows_the_path_and_gives_it_back():
    """web/ and poller/ share five module names. With poller/ left in front, uvicorn re-imports
    poller/main.py — no `app`, and the web does not boot at all. Measured, not feared."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()
    body = src.split("def _ensure_schema()", 1)[1].split("\n_ensure_schema()", 1)[0]
    assert "sys.path.insert" in body and "sys.path.remove" in body
    assert "finally:" in body.split("sys.path.insert", 1)[1].split("import schema", 1)[1][:120]


def test_the_module_it_loads_imports_nothing():
    """poller/schema.py is dependency-free ON PURPOSE: `db.py` pulls in crypto and geohash, and the
    web has files by both names — geohash differs between them by 64 lines. A schema module that
    imports nothing cannot pick up the wrong one."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "poller" / "schema.py").read_text()
    tops = [l for l in src.split("\n") if l.startswith(("import ", "from "))]
    assert tops == [], f"poller/schema.py must import nothing, found: {tops}"


def test_it_does_not_drag_the_data_repairs_along():
    """Database.__init__ also deletes phantom rows and migrates the secrets. Those belong to the
    process that owns the data — a reader running them, concurrently with the poller, is a much
    bigger thing than a missing column."""
    import ast
    src = (pathlib.Path(__file__).resolve().parent.parent / "poller" / "schema.py").read_text()
    # Parsed, not grepped. The module's own comments name these functions to explain what it leaves
    # behind — including one written with parentheses — so a regex over the raw text fails on the
    # explanation rather than on the code. An AST sees calls and nothing else.
    called = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(getattr(f, "attr", None) or getattr(f, "id", None))
    bad = sorted(c for c in called if c and (c.startswith(("_repair_", "_backfill_", "_drop_phantom"))
                                             or c == "migrate_secrets"))
    assert not bad, f"the schema module must not run data repairs: {bad}"

