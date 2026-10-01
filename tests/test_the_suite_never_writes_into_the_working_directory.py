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


_PROBE = """import os, sys
sys.stderr.write("DBPATH " + os.environ.get("DB_PATH", "") + "\\n")   # at collection, which every worker does itself
def test_a(): pass
"""


def _db_paths_seen(tmp_path, *args, **env):
    """DB_PATH as a real pytest run sees it, one entry per process that collected the probe, with
    conftest loaded as a plugin and exactly `env` on top of a shell that carries no DB_PATH and
    no PYTEST_ADDOPTS of its own."""
    import subprocess
    import sys
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE)
    clean = {k: v for k, v in os.environ.items() if k not in ("DB_PATH", "PYTEST_ADDOPTS")}
    clean["PYTHONPATH"] = str(pathlib.Path(__file__).parent)
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", "-p", "conftest",
                          "--rootdir", str(tmp_path), str(probe), *args],
                         cwd=tmp_path, env={**clean, **env}, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    return [line.split(" ", 1)[1] for line in out.stderr.splitlines() if line.startswith("DBPATH ")]


def test_a_run_without_a_path_gets_a_file_outside_the_working_directory(tmp_path):
    seen = _db_paths_seen(tmp_path)
    assert len(seen) == 1 and seen[0]
    assert not pathlib.Path(seen[0]).is_relative_to(tmp_path)


def test_two_xdist_workers_do_not_share_a_database(tmp_path):
    seen = _db_paths_seen(tmp_path, "-n", "2")
    assert len(seen) == 2 and seen[0] != seen[1], seen


def test_a_path_somebody_set_on_purpose_reaches_every_worker(tmp_path):
    mine = str(tmp_path / "mine.db")
    assert _db_paths_seen(tmp_path, "-n", "2", DB_PATH=mine) == [mine, mine]


def test_a_controller_that_only_collects_has_a_path_too(tmp_path):
    """Under --collect-only xdist hands nothing out and the controller imports every module itself."""
    seen = _db_paths_seen(tmp_path, "-n", "2", "--collect-only")
    assert len(seen) == 1 and seen[0]
