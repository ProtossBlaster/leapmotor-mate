"""Paths derive from the configured database, never a laboratory mount."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    db: Path
    data: Path
    cert_dir: Path
    ca: Path


def paths():
    db = Path(os.environ.get('DB_PATH', '/data/leapmotor_mate.db')).resolve()
    return Paths(db, db.parent, db.parent / 'certs', Path(__file__).with_name('leapmotor-appsubca-public.pem'))


def configure():
    p = paths()
    os.environ.setdefault('DATA_CERT_DIR', str(p.cert_dir))
    os.environ.setdefault('CERT_DIR', str(p.cert_dir))
    os.environ.setdefault('TZ', 'Europe/Rome')


def prepare_installation():
    from migration_state import backup_before_migration
    from bootstrap_independent import bootstrap
    p = paths()
    p.data.mkdir(parents=True, exist_ok=True)
    if os.environ.get('MATE_DEMO', '').lower() in ('1', 'true'):
        return {'state': 'demo'}
    backup_before_migration(p.db)
    source = os.environ.get('MATE_APPLICATION_BUNDLE')
    if source:
        return bootstrap(source=source, destination=p.data)
    # Do not prevent the setup UI from rendering on a genuinely empty install.
    from setup_readiness import readiness
    return readiness(p.cert_dir)
