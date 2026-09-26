"""Product migration: persistent data, portable paths and explicit startup."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'poller' / 'mate_api_runtime'))

class ProductMigrationTests(unittest.TestCase):
    def test_backup_is_consistent_private_and_idempotent(self):
        from migration_state import backup_before_migration
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); db=root/'custom.db'
            with closing(sqlite3.connect(db)) as c:
                c.execute('create table trips(id integer, energy real)')
                c.execute('insert into trips values(1,0)')
                c.commit()
            (root/'secret.key').write_bytes(b'synthetic-key')
            result=backup_before_migration(db)
            self.assertEqual(result, backup_before_migration(db))
            with closing(sqlite3.connect(result/db.name)) as c:
                self.assertEqual(c.execute('select * from trips').fetchall(),[(1,0.0)])
            self.assertEqual((result/'secret.key').read_bytes(),b'synthetic-key')
            if os.name!='nt': self.assertEqual(result.stat().st_mode & 0o777,0o700)
            self.assertTrue((result/'complete.json').is_file())

    def test_failed_backup_never_marks_complete(self):
        from migration_state import backup_before_migration
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'custom.db';db.write_bytes(b'not sqlite')
            with self.assertRaises(sqlite3.DatabaseError):backup_before_migration(db)
            self.assertFalse(list(Path(tmp).rglob('complete.json')))
            self.assertEqual(db.read_bytes(),b'not sqlite')

    def test_runtime_paths_work_outside_container(self):
        from runtime_paths import paths
        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch
            with patch.dict(os.environ,{'DB_PATH':str(Path(tmp)/'my.db')},clear=True):
                p=paths()
                self.assertEqual(p.data,Path(tmp).resolve())
                self.assertEqual(p.cert_dir,Path(tmp).resolve()/'certs')
                self.assertTrue(p.ca.is_file())

    def test_process_lock_releases_after_exception(self):
        from process_lock import exclusive
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'lock'
            with self.assertRaises(RuntimeError):
                with exclusive(p):raise RuntimeError('test')
            with exclusive(p):self.assertTrue(p.is_file())

    def test_vendored_library_matches_manifest(self):
        import hashlib
        root=ROOT/'poller'/'vendor'
        manifest=json.loads((root/'mate-api.json').read_text())
        self.assertEqual(manifest['version'],'0.1.0a9')
        for name,digest in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest,name)

    def test_two_processes_share_one_complete_backup(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'custom.db'
            with closing(sqlite3.connect(db)) as c:
                c.execute('create table trips(id integer)')
                c.commit()
            env = dict(os.environ, PYTHONPATH=os.pathsep.join((
                str(ROOT/'poller'/'vendor'), str(ROOT/'poller'/'mate_api_runtime'))))
            code = 'import sys; from migration_state import backup_before_migration; backup_before_migration(sys.argv[1])'
            workers = [subprocess.Popen([sys.executable, '-c', code, str(db)], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
            results = [worker.communicate(timeout=20) for worker in workers]
            for worker, (out, err) in zip(workers, results):
                self.assertEqual(worker.returncode, 0, err.decode())
            self.assertEqual(len(list(root.rglob('complete.json'))), 1)
            self.assertFalse(list(root.rglob('.pending-*')))

    def test_reuse_refuses_backup_with_missing_database(self):
        from migration_state import backup_before_migration
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'custom.db'
            with closing(sqlite3.connect(db)) as c:
                c.execute('create table trips(id integer)')
                c.commit()
            result = backup_before_migration(db)
            (result/db.name).unlink()
            with self.assertRaises(ValueError):
                backup_before_migration(db)
