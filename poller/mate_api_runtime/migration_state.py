"""One-time, consistent pre-migration backup. Original data is never rewritten."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from process_lock import exclusive
from leapmotor_cloud.private_storage import ensure_private_directory


def backup_before_migration(database):
    database = Path(database).resolve()
    root = database.parent
    root.mkdir(parents=True, exist_ok=True)
    final = root / 'migration-backups' / 'mate-4.0.0'
    with exclusive(root / '.mate-upgrade.lock'):
        if (final / 'complete.json').is_file():
            try:
                saved = json.loads((final / 'complete.json').read_text())
                if (saved['format'] != 1 or saved['database'] != database.name
                        or saved['mate_target'] != '4.0.0'
                        or (saved['database_existed'] and not (final / database.name).is_file())):
                    raise ValueError('Incomplete migration backup')
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError('Invalid migration backup') from exc
            return final
        if final.exists():
            raise ValueError('Incomplete backup requires inspection')
        ensure_private_directory(final.parent)
        stage = Path(tempfile.mkdtemp(prefix='.pending-', dir=final.parent))
        try:
            ensure_private_directory(stage)
            if database.exists():
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
                    with closing(sqlite3.connect(stage / database.name)) as destination:
                        source.backup(destination)
                        if destination.execute('pragma integrity_check').fetchone()[0] != 'ok':
                            raise ValueError('Backup integrity failed')
            for name in ('secret.key', 'certs', 'api-v2-private', 'api-v2-account-material'):
                path = root / name
                if path.is_symlink():
                    raise ValueError('Unsafe backup source')
                if path.is_dir():
                    if any(item.is_symlink() for item in path.rglob('*')):
                        raise ValueError('Unsafe material source')
                    shutil.copytree(path, stage / name)
                elif path.is_file():
                    shutil.copy2(path, stage / name)
            for path in stage.rglob('*'):
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
                if path.is_file():
                    with path.open('r+b') as stream:
                        os.fsync(stream.fileno())
            manifest = {'database': database.name, 'format': 1, 'mate_target': '4.0.0',
                        'database_existed': database.exists()}
            marker = stage / 'complete.json'
            with marker.open('x') as stream:
                json.dump(manifest, stream)
                stream.flush(); os.fsync(stream.fileno())
            os.chmod(marker, 0o600)
            if os.name != 'nt':
                # Persist directory entries as well as file contents before publishing.
                for directory in [p for p in stage.rglob('*') if p.is_dir()] + [stage]:
                    fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            stage.rename(final)
            if os.name != 'nt':
                fd = os.open(final.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return final
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
