"""The lock the poller and the web share keeps the other process out on a Docker Desktop mount too.

On a fresh /data the two processes install the Leapmotor app certificate at the same moment, and
`process_lock.exclusive` was the only thing that put one after the other. It took flock(), which a
Docker Desktop bind mount on macOS does not honour between processes: measured on 07/11/2026 with
the 4.23.0 image, a second process got the lock while the first held it, one install refused to
overwrite the other's, and a container started from the README's Compose on an empty ./data
restarted for ever on "No such file or directory: '/data/certs/app.crt'". A POSIX lock (lockf, the
kind SQLite takes) does hold on that mount, once the file exists, so `exclusive` now takes one too.

And because two processes that CREATE the lock file at the same instant were not kept apart there by
either lock, run.sh prepares the installation once, before it starts the two of them.
"""
import fcntl
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX locks; Windows takes msvcrt.locking")


def test_a_process_holding_the_lock_holds_a_posix_lock(tmp_path):
    lock = tmp_path / ".automatic-material.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(ROOT / 'poller' / 'mate_api_runtime')!r})
            from process_lock import exclusive
            with exclusive({str(lock)!r}):
                print("held", flush=True)
                sys.stdin.readline()
        """)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        fd = os.open(lock, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    finally:
        holder.communicate("\n", timeout=10)


def test_the_lock_is_free_again_once_released(tmp_path):
    sys.path.insert(0, str(ROOT / "poller" / "mate_api_runtime"))
    try:
        from process_lock import exclusive
    finally:
        sys.path.pop(0)
    lock = tmp_path / "x.lock"
    with exclusive(lock):
        pass
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_run_sh_prepares_the_installation_before_it_starts_the_two_services():
    script = (ROOT / "run.sh").read_text()
    normal = script[script.index('echo "[LeapMotor Mate] Starting..."'):]
    prepare = normal.find("prepare_installation()")
    poller = normal.find("python3 /app/poller/main.py &")
    web = normal.find("python3 /app/web/main.py &")
    assert prepare != -1 and poller != -1 and web != -1
    assert prepare < poller and prepare < web
    # It must not stop Mate when it fails: each service still prepares on its own and shows the state.
    line = next(l for l in normal.splitlines() if "prepare_installation()" in l)
    assert re.search(r"\|\|", line) or "|| " in normal[normal.index(line):normal.index(line) + 400]

