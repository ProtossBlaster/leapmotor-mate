"""Native interprocess lock for Linux/macOS and Windows; no cloud side effects."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time


@contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Unsafe lock path')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b') as lock:
        if os.name == 'nt':
            import msvcrt
            # The lock file stays empty on purpose, and acquisition never writes to
            # it. Windows byte-range locks are mandatory and valid past end of file,
            # so locking byte 0 of an empty file works and gives the holder exclusive
            # access to it. Seeding that byte before the lock attempt below therefore
            # races: once a peer holds byte 0, the write fails with
            # ERROR_LOCK_VIOLATION, which the CRT reports as PermissionError
            # [Errno 13] — fatal, because it happens outside the retry loop.
            # → test_taking_the_lock_never_writes_to_the_lock_file
            deadline = time.monotonic() + 120
            while True:
                try:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
