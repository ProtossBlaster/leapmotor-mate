"""Select one migration backend before either application process starts.

A successful qualification promotes only session settings. A failed qualification
keeps the legacy client for this release; remote commands never trigger fallback.
"""
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from process_lock import exclusive
from leapmotor_cloud.private_storage import ensure_private_directory

# The decision is kept per release and returned untouched while this matches, so an
# installation is asked again only when what qualifies has changed. It changed here: until
# 4.3.1 the parent accepted a qualification only from an account whose every car was a B10,
# so every other account was left on the bundled SDK and never asked again.
RELEASE = '4.4.0'
DECISION_KEY = 'mate_api_migration_decision'

# …but a `legacy` decision is not one of those answers. Since 4.2.0 every model qualifies
# (`_qualify_staged`: "Every model qualifies") and 4.4.0 removed the literal `['B10']` that had kept
# the others out, so nothing an account can be makes it fail: every stored `legacy` decision is a
# qualification that did not FINISH — a timeout, a database locked by the other process, a login the
# cloud refused that minute. Keeping a missing answer for the life of a release pinned such an
# installation to the bundled SDK for ever, and RELEASE has not moved since 4.4.0: @dommi1966 (#338)
# is the only public bundle still reading `bundled SDK`, and his first one showed 747
# `database is locked` — exactly the state that makes the qualification fail.
#
# So it is re-attempted; just not on every start, because the qualification performs a live cloud
# login and that cloud rations them (#296). A decision written before this rule carries no
# `attempted` at all and is re-attempted at once, which is what moves the installations stuck since
# 4.4.0. A QUALIFIED decision is untouched by any of this: it promoted session material, it is
# expensive, and it stays.
# → tests/test_a_qualification_that_did_not_finish_is_asked_again.py
RETRY_AFTER_S = 6 * 3600


def _settings(database):
    if not database.is_file():
        return {}
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return {}
        return dict(db.execute('SELECT key,value FROM settings'))


def _identity(values):
    # Stable across the existing plaintext-to-encrypted settings upgrade.
    # Decrypting plaintext does not create a key. No cleartext is persisted.
    import crypto
    account = [crypto.decrypt(values.get('leapmotor_user', '')) or os.environ.get('LEAPMOTOR_USER', ''),
               crypto.decrypt(values.get('leapmotor_pass', '')) or os.environ.get('LEAPMOTOR_PASS', '')]
    from runtime_paths import paths
    key_file = paths().data / 'secret.key'
    key = os.environ.get('MATE_SECRET_KEY', '').encode() or (key_file.read_bytes() if key_file.exists() else b'')
    if not key:
        # A legacy plaintext installation has no secret yet. The promotion
        # recomputes this once its complete key has been published.
        return 'pending:' + hashlib.sha256(account[0].encode()).hexdigest()
    return hmac.new(key, json.dumps(account).encode(), hashlib.sha256).hexdigest()


def _select(result):
    os.environ['MATE_API_V2'] = '1' if result['backend'] == 'independent' else '0'
    return result


def _save_decision(database, decision, promoted=None):
    with closing(sqlite3.connect(database, timeout=2)) as db:
        with db:
            if promoted:
                db.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', promoted.items())
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)',
                       (DECISION_KEY, json.dumps(decision, sort_keys=True)))


def _promote(database, stage, decision):
    from migration_preflight import SESSION_SETTINGS
    values = _settings(stage / database.name)
    if not values.get('api_v2_shared_session'):
        raise ValueError('Qualified session missing')
    selected = {key: value for key, value in values.items()
                if key in SESSION_SETTINGS or key.startswith('api_v2_access_')}
    # Legacy plaintext installations may not yet have a key. Publish the worker's
    # key before the atomic settings transaction; never overwrite an existing key.
    source_key, target_key = stage / 'secret.key', database.parent / 'secret.key'
    if source_key.exists() and not target_key.exists():
        fd, pending = tempfile.mkstemp(prefix='.migration-key-', dir=database.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(source_key.read_bytes()); stream.flush(); os.fsync(stream.fileno())
            # All startup writers hold .mate-backend.lock. Publish a complete key,
            # never an empty/truncated live file if the process is interrupted.
            if target_key.exists():
                if target_key.read_bytes() != source_key.read_bytes():
                    raise ValueError('Installation key changed during qualification')
            else:
                os.replace(pending, target_key)
        finally:
            Path(pending).unlink(missing_ok=True)
    elif source_key.exists() and source_key.read_bytes() != target_key.read_bytes():
        raise ValueError('Installation key changed during qualification')
    # The worker already preserved the pre-migration backup. Discard the large
    # qualification database after reading settings, before flushing small material.
    for suffix in ('', '-wal', '-shm'):
        (stage / (database.name + suffix)).unlink(missing_ok=True)
    # Account certificate paths point into the private, durable generation. Fsync
    # it before publishing the session so an interrupted promotion is retryable.
    for path in stage.rglob('*'):
        if path.is_symlink():
            raise ValueError('Unsafe migration generation')
        if path.is_file():
            with path.open('r+b') as stream:
                os.fsync(stream.fileno())
    if os.name != 'nt':
        for path in [p for p in stage.rglob('*') if p.is_dir()] + [stage, stage.parent, database.parent]:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    decision['identity'] = _identity(_settings(database))
    _save_decision(database, decision, selected)
    # Keep the successful material generation: session certificates reference it.
    # No unrelated history is restored.


def activate_installation():
    from runtime_paths import paths
    from migration_preflight import qualify_installation
    database = paths().db
    root = database.parent
    if os.environ.get('MATE_DEMO', '').lower() in ('1', 'true') or (root / 'demo.flag').exists():
        return _select({'backend': 'independent', 'state': 'demo'})
    initial = _settings(database)
    if not initial or not (initial.get('leapmotor_user') or os.environ.get('LEAPMOTOR_USER')):
        return _select({'backend': 'independent', 'state': 'new_installation'})
    with exclusive(root / '.mate-backend.lock'):
        values = _settings(database)
        identity = _identity(values)
        try:
            previous = json.loads(values.get(DECISION_KEY, '{}'))
        except (ValueError, TypeError):
            previous = {}
        if (previous.get('release') == RELEASE and previous.get('identity') == identity
                and (previous.get('backend') == 'independent'
                     or (previous.get('backend') == 'legacy'
                         and time.time() - previous.get('attempted', 0) < RETRY_AFTER_S))):
            return _select(previous)
        decision = dict(release=RELEASE, identity=identity, backend='legacy', state='retained',
                        attempted=time.time())
        stage = None
        try:
            generations = root / '.api-migration-generations'
            ensure_private_directory(generations)
            stage = Path(tempfile.mkdtemp(prefix='generation-', dir=generations))
            ensure_private_directory(stage)
            result = qualify_installation(database, stage)
            if result['state'] == 'qualified':
                decision.update(backend='independent', state='qualified')
                decision.pop('attempted', None)
                _promote(database, stage, decision)
                return _select(decision)
            decision['reason'] = result.get('reason', 'qualification_unavailable')
        except Exception:
            # No exception or external payload is stored/logged: it may include
            # credentials. Keep the installed identity and the working backend.
            decision.update(backend='legacy', state='retained', reason='qualification_failed')
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        _save_decision(database, decision)
        return _select(decision)
