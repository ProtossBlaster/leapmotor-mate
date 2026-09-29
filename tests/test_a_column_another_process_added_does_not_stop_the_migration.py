"""Two Mate processes upgrade the same database at the same time, and both survive it.

`ensure_schema` is run by the poller AND by the web — that is its whole reason for existing (the
web "merely hoped" the poller had run). On an install being upgraded, both read
`PRAGMA table_info(positions)` at boot, both see a column missing, and both issue the same
`ALTER TABLE ... ADD COLUMN`. One of them arrives second and SQLite answers
`duplicate column name`, which is not a problem — the column is there, which is all anyone wanted.

What WAS a problem is that the exception left the function, so every migration after that point
never ran for that process. Measured on @dommi1966's install (#338, 29/09/2026), whose web log
carries exactly one line:

    [WARNING] mate.web: Schema check skipped: duplicate column name: ac_port_mode

`ac_port_mode` sits two thirds of the way down; `fan_level`, `recirculation`, `climate_mode`,
the REEV columns, `fuel_liters`, `frame_ts`, `abilities` and every trips/trip_positions migration
come after it, and none of them were attempted.

🔑 The race is provoked, not waited for. Two real threads would hit this window only sometimes,
and a test that fails one run in twenty teaches nobody anything: here the second writer lands in
the window on purpose, between the PRAGMA that reads the columns and the ALTER that adds one.
Everything else is real — a real SQLite file, the real `ensure_schema`, the real error.
"""
import sqlite3

import pytest

import schema as S

# Two columns `ensure_schema` adds AFTER ac_port_mode. They are what says the migration carried on.
AFTER = ("fan_level", "recirculation", "climate_mode", "fuel_liters", "frame_ts")


def _old_positions(path):
    """A `positions` table as an install from before these migrations has it: created by the real
    SCHEMA, then stripped back to the columns that predate the ALTERs."""
    conn = sqlite3.connect(path)
    conn.executescript(S.SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)")]
    for c in ("ac_port_mode", *AFTER):
        if c in cols:
            conn.execute(f"ALTER TABLE positions DROP COLUMN {c}")
    conn.commit()
    conn.close()


class _Taken(list):
    """The rows as the caller would have got them: `ensure_schema` both iterates a cursor and calls
    `.fetchall()` on one, so the stand-in has to answer to both."""

    def fetchall(self):
        return list(self)


class _OtherProcess(sqlite3.Connection):
    """A connection that lets the OTHER Mate process in, once, at the worst moment: right after the
    columns of `positions` have been read, so the decision `ensure_schema` is about to make on them
    is already stale."""

    def __init__(self, path, *a, **kw):
        super().__init__(path, *a, **kw)
        self._path = path
        self._raced = False

    def execute(self, sql, *a, **kw):
        cur = super().execute(sql, *a, **kw)
        if not self._raced and "table_info(positions)" in sql:
            self._raced = True
            rows = cur.fetchall()
            other = sqlite3.connect(self._path)
            other.execute("ALTER TABLE positions ADD COLUMN ac_port_mode INTEGER DEFAULT NULL")
            other.commit()
            other.close()
            return _Taken(rows)        # give ensure_schema the reading it actually took
        return cur


def test_the_migration_finishes_when_the_other_process_won_a_column(tmp_path):
    path = str(tmp_path / "mate.db")
    _old_positions(path)

    conn = sqlite3.connect(path, factory=_OtherProcess)
    try:
        S.ensure_schema(conn)          # must not raise: the column is THERE, which is the point
        cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
    finally:
        conn.close()

    assert "ac_port_mode" in cols
    missing = [c for c in AFTER if c not in cols]
    assert not missing, f"the migration stopped at ac_port_mode — never added {missing}"


def test_a_second_run_over_a_finished_database_still_changes_nothing(tmp_path):
    """The repair must not become a blanket 'ignore errors': a plain second run is still the
    cheap no-op the docstring promises, and it still raises on a database it cannot migrate."""
    path = str(tmp_path / "mate.db")
    _old_positions(path)
    conn = sqlite3.connect(path)
    try:
        S.ensure_schema(conn)
        before = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
        S.ensure_schema(conn)
        assert {r[1] for r in conn.execute("PRAGMA table_info(positions)")} == before

        conn.execute("DROP TABLE positions")
        conn.commit()
        conn.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY)")
        conn.commit()
        with pytest.raises(sqlite3.Error):
            # `positions` without `vehicle_id` cannot take the indexes the schema builds on it:
            # a database this broken must still say so, not be swallowed by the new tolerance.
            S.ensure_schema(conn)
    finally:
        conn.close()
