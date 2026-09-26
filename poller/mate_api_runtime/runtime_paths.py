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
    cert_dir = Path(os.environ.get('DATA_CERT_DIR') or os.environ.get('CERT_DIR') or db.parent / 'certs').absolute()
    return Paths(db, db.parent, cert_dir, Path(__file__).with_name('leapmotor-appsubca-public.pem'))


def configure():
    p = paths()
    os.environ.setdefault('DATA_CERT_DIR', str(p.cert_dir))
    os.environ.setdefault('CERT_DIR', str(p.cert_dir))
    os.environ.setdefault('TZ', 'Europe/Rome')


def prepare_installation():
    if os.environ.get("MATE_API_V2") == "0":
        return {"state": "legacy"}
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
    from automatic_material import provision_automatic
    candidates = [p.cert_dir, Path(os.environ.get('CERT_DIR') or p.cert_dir), p.data / 'certs']
    certificate_directory = next((directory for directory in candidates
                                  if all((directory / name).is_file() for name in ('app.crt', 'app.key'))), p.cert_dir)
    has_material = any((directory / name).exists() or (directory / name).is_symlink()
                       for directory in candidates for name in ('app.crt', 'app.key'))
    if has_material or (p.data / '.application-transaction').exists():
        return provision_automatic(p.data, certificate_directory=certificate_directory)
    # A genuinely fresh install still offers its normal certificate/account setup.
    from setup_readiness import readiness
    return readiness(p.cert_dir, parameters_directory=p.data / 'api-v2-private')
