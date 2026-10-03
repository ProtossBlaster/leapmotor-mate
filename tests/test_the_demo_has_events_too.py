"""The demo database carries events, so the Events page has something to show without a poller.

The demo runs the web alone; the seed script does the poller's part of the job itself, on the
richer frames it writes (the cabin heated before a commute and cooled on the way to the sea, the
tailgate open after it, a cable in at the wallbox hours before its schedule).
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "poller" / "seed_demo.py"


def test_the_seeded_demo_has_spans_of_the_everyday_kinds(tmp_path):
    path = str(tmp_path / "demo.db")
    # Its own process, as run.sh starts it: importing it here would put poller/ ahead of web/ on sys.path.
    subprocess.run([sys.executable, str(SEED)], env={**os.environ, "DB_PATH": path}, check=True, capture_output=True)
    con = sqlite3.connect(path)
    kinds = {r[0] for r in con.execute("SELECT DISTINCT kind FROM events")}
    assert {"unlocked", "cable", "climate", "trunk"} <= kinds
    assert con.execute("SELECT COUNT(*) FROM events WHERE kind = 'climate' AND state = 1 AND inside_temp IS NOT NULL"
                       " AND climate_target_temp IS NOT NULL AND outside_temp IS NOT NULL").fetchone()[0] > 5
    assert con.execute("SELECT COUNT(*) FROM events WHERE state = 1").fetchone()[0] > 50
    assert con.execute("SELECT value FROM settings WHERE key = 'events_state_1'").fetchone()
