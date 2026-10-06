"""A NAS shared folder as /data does not stop Mate at startup (#401).

@awooganl's report: with a Synology shared folder mounted on /data, every image from 4.0.0 stopped
at once with «ValueError: Private directory permissions required», and 3.19.2 ran. Storage that
does not keep file permissions gives that error: measured here on a FAT disk, where a directory
made 0700 reads back 0777, chmod does not change it, and 4.10.0 stopped in the one-time backup
before the migration. A NAS shared folder that maps its own ACLs onto the mode can behave the same
way (not tried on a Synology); there the folder's own permissions decide who reads it. mate-api
0.1.0a16 tells that storage from an exposed folder with a probe; the backup here also lets a
refused chmod pass (a case only these tests make), since the folder holding the copy has already
been accepted.
"""
import logging
import os
import sqlite3
from contextlib import closing, contextmanager
from unittest.mock import patch

import pytest

import mate_api  # noqa: F401 — puts poller/vendor and mate_api_runtime on sys.path
from leapmotor_cloud import private_storage
from migration_state import backup_before_migration

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='POSIX permission bits')


@pytest.fixture(autouse=True)
def _first_meeting(monkeypatch):
    """Each test meets its storage for the first time: the probe's verdict is kept per folder."""
    monkeypatch.setattr(private_storage, '_MODES_KEPT', {}, raising=False)


@contextmanager
def acl_share(root, *, chmod_refused=False):
    """Everything under `root` reads back 0777, whatever was asked, as the FAT disk did (#401)."""
    real_stat, real_fstat, real_chmod = os.stat, os.fstat, os.chmod

    def widen(result):
        kind, (fields, extra) = result.__reduce__()   # keeps the nanosecond times copystat reads
        return kind((fields[0] | 0o777,) + tuple(fields[1:]), extra)

    def stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        return widen(result) if os.fspath(path).startswith(os.fspath(root)) else result

    def chmod(path, mode, *args, **kwargs):
        if chmod_refused and os.fspath(path).startswith(os.fspath(root)):
            raise PermissionError(1, 'Operation not permitted', os.fspath(path))
        return real_chmod(path, mode, *args, **kwargs)

    with patch('os.stat', stat), patch('os.fstat', lambda fd: widen(real_fstat(fd))), \
            patch('os.chmod', chmod):
        yield


def _data(root):
    db = root / 'custom.db'
    with closing(sqlite3.connect(db)) as c:
        c.execute('create table trips(id integer)')
        c.execute('insert into trips values(1)')
        c.commit()
    (root / 'secret.key').write_bytes(b'synthetic-key')
    (root / 'certs').mkdir()
    (root / 'certs' / 'app.crt').write_bytes(b'synthetic-certificate')
    return db


@pytest.mark.parametrize('chmod_refused', [False, True], ids=['chmod ignored', 'chmod refused'])
def test_the_startup_backup_completes_on_a_shared_folder(tmp_path, caplog, chmod_refused):
    db = _data(tmp_path)
    with acl_share(tmp_path, chmod_refused=chmod_refused), \
            caplog.at_level(logging.WARNING, logger='leapmotor_cloud.private_storage'):
        final = backup_before_migration(db)
        assert backup_before_migration(db) == final, 'the second start finds it complete'
    assert (final / 'complete.json').is_file()
    with closing(sqlite3.connect(final / db.name)) as c:
        assert c.execute('select * from trips').fetchall() == [(1,)]
    assert (final / 'secret.key').read_bytes() == b'synthetic-key'
    assert (final / 'certs' / 'app.crt').read_bytes() == b'synthetic-certificate'
    said = [r.getMessage() for r in caplog.records]
    assert len(said) == 1 and 'does not keep file permissions' in said[0]
    assert not list(tmp_path.rglob('.mode-probe-*')), 'the probe leaves nothing behind'


def test_an_exposed_folder_on_a_normal_disk_is_still_refused(tmp_path):
    db = _data(tmp_path)
    (tmp_path / 'migration-backups').mkdir(mode=0o755)
    (tmp_path / 'migration-backups').chmod(0o755)
    with pytest.raises(ValueError, match='Private directory permissions required'):
        backup_before_migration(db)
