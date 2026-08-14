"""No test may open the database at its DEFAULT path — whatever happens to be there.

`db_reader.DB_PATH` and `Database()` both default to a RELATIVE "leapmotor_mate.db", resolved
against the current directory. Run the suite from the repo and 30 of its files created and wrote a
610 KB database there; run it from a directory where a real Mate database sits — a bind-mount
folder, a copy taken for triage — and those same tests write into that one instead.

Two costs, and the second is the one that bites:

  * tests share a database nobody reset between them, so what passes here can fail in CI (or pass
    for a reason that has nothing to do with the code under test);
  * a test suite must never be able to touch data it did not create.

conftest.py now points DB_PATH at a temporary file for the whole session, before anything imports
db_reader. This file is what keeps it true: it fails the moment the default is relative again, or
the environment stops being set.
"""
import os
import pathlib

import db_reader


def test_the_configured_path_is_not_in_the_working_directory():
    p = pathlib.Path(db_reader.DB_PATH)
    assert p.is_absolute(), f"DB_PATH is relative — it follows the shell: {db_reader.DB_PATH!r}"
    assert pathlib.Path.cwd() not in p.parents, f"the suite would write into {p.parent}"


def test_the_repository_is_not_the_database_directory():
    """The specific accident that was happening: the checkout itself as the data directory."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    assert repo not in pathlib.Path(db_reader.DB_PATH).parents


def test_the_environment_carries_it_so_a_subprocess_inherits_it():
    """Some tests spawn the app in another process; an in-memory monkeypatch would not follow."""
    assert os.environ.get("DB_PATH") == db_reader.DB_PATH


def test_the_suite_writes_to_no_database_that_was_already_there():
    """The end state, checked against what the run started with rather than against an empty
    directory: a stale file from an older run is not this suite's to delete, and one of these could
    be somebody's real data. What must be true is that nothing HERE wrote to any of them."""
    import conftest
    repo = pathlib.Path(__file__).resolve().parent.parent
    touched = [p.name for p in repo.glob("*.db")
               if p.name in conftest.DB_FILES_AT_START
               and p.stat().st_mtime != conftest.DB_FILES_AT_START[p.name]]
    assert touched == [], f"the suite wrote into {touched}, which it did not create"
    created = [p.name for p in repo.glob("*.db") if p.name not in conftest.DB_FILES_AT_START]
    assert created == [], f"the suite created {created} in the repository"
