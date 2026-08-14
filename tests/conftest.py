"""Put poller/ and web/ on sys.path so tests can import their modules by bare name
(the two dirs are separate import roots in the container, mirrored here) — and keep the suite's
database out of whatever directory it happens to be run from."""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _d in ("poller", "web"):
    p = str(ROOT / _d)
    if p not in sys.path:
        sys.path.insert(0, p)

# `db_reader.DB_PATH` and `Database()` default to a RELATIVE "leapmotor_mate.db", so a test that
# forgets to point them somewhere opens whatever sits in the current directory. Running the suite
# from the repo, 30 files created and wrote a 610 KB database there; run from a folder holding a
# real Mate database — a bind-mount, a copy taken for triage — the same tests would write into
# that one. Set here, and NOT in a fixture, because it has to be true before the first import:
# db_reader reads the variable at module level, once. `setdefault` leaves an explicit DB_PATH
# alone, which is how a test that wants a specific file still gets it.
# → tests/test_the_suite_never_writes_into_the_working_directory.py
os.environ.setdefault("DB_PATH", str(pathlib.Path(
    tempfile.mkdtemp(prefix="mate-suite-")) / "leapmotor_mate.db"))

# Whatever databases were sitting in the repository when the suite started, and when they were last
# written. A test compares against this: the suite must leave every one of them untouched, because
# one of them might be somebody's real data.
DB_FILES_AT_START = {p.name: p.stat().st_mtime for p in ROOT.glob("*.db")}
