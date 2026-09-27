"""4.2.0 opened the gate inside the child and left it shut in the parent (#298, #327, #330).

"Cloud commands reach every model" was true of the command gate. It was not true of the
decision that comes first: WHICH CLIENT an installation runs. That is settled by
`qualify_installation`, which runs the probe in a subprocess and then reads its verdict — and
the parent accepted only one verdict, literally:

    if result == {'state': 'qualified', 'capabilities': ['B10']}:

while the child, since 4.2.0, answers with the models it actually saw and says so in its own
comment: *Every model qualifies.* A C10 account therefore answered `['C10']`, matched nothing,
fell through to `qualification_failed`, and was kept on the bundled SDK — where the
consumption reads went out unsigned (#327), where there is no cloud-trip-history card (#298),
and where @arzthilfe and @adoewa have been all along.

Worse, the verdict is sticky: `activate_installation` returns a saved decision untouched while
its `release` matches, and `RELEASE` has read '4.0.0' through 4.1, 4.2, 4.3 and 4.3.1. So an
installation that failed once — on a day the cloud was refusing logins, say — was never asked
again by any later version.
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'poller' / 'mate_api_runtime'
sys.path[:0] = [str(RUNTIME), str(ROOT / 'poller' / 'vendor')]


def _module():
    import migration_preflight
    return migration_preflight


def _database(tmp_path):
    root = tmp_path / 'live'
    root.mkdir()
    db = root / 'mate.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)')
        conn.executemany('INSERT INTO settings VALUES (?,?)',
                         [('leapmotor_user', 'fixture@example.invalid'),
                          ('leapmotor_pass', 'fixture-password')])
    (root / 'secret.key').write_bytes(b'original-key')
    return db


def _answering(migration, monkeypatch, verdict):
    """Run the probe for real up to the child, and let the child answer `verdict`."""
    def child(command, **kwargs):
        stage = Path(kwargs['env']['DB_PATH'])
        migration._snapshot(_DB[0], stage.parent)
        Path(kwargs['env']['MATE_PREFLIGHT_RESULT']).write_text(json.dumps(verdict))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(migration.subprocess, 'run', child)


_DB = [None]


@pytest.mark.parametrize('models', [['C10'], ['T03'], ['B05'], ['B10', 'C10'], ['C10', 'C11', 'T03']])
def test_an_account_of_any_model_qualifies(tmp_path, monkeypatch, models):
    migration = _module()
    _DB[0] = _database(tmp_path)
    _answering(migration, monkeypatch, {'state': 'qualified', 'capabilities': models})
    result = migration.qualify_installation(_DB[0], tmp_path / 'stage')
    assert result == {'state': 'qualified', 'capabilities': models}


def test_a_verdict_that_is_not_a_qualification_is_still_refused(tmp_path, monkeypatch):
    migration = _module()
    _DB[0] = _database(tmp_path)
    for verdict in ({'state': 'failed', 'reason': 'telemetry'},
                    {'state': 'qualified'},
                    {'state': 'qualified', 'capabilities': 'C10'},
                    {'state': 'qualified', 'capabilities': []},
                    {'capabilities': ['C10']}):
        _answering(migration, monkeypatch, verdict)
        result = migration.qualify_installation(_DB[0], tmp_path / f'stage-{id(verdict)}')
        assert result['state'] == 'failed', verdict


def test_not_required_still_passes_through(tmp_path, monkeypatch):
    migration = _module()
    _DB[0] = _database(tmp_path)
    _answering(migration, monkeypatch, {'state': 'not_required'})
    assert migration.qualify_installation(_DB[0], tmp_path / 'stage') == {'state': 'not_required'}


def test_a_decision_taken_by_an_earlier_release_is_asked_again():
    """The verdict is kept per release, and this is the release that changed what qualifies:
    an installation refused under the old rule has to be given its answer once more."""
    import migration_activation
    assert migration_activation.RELEASE != '4.0.0', (
        'RELEASE still reads 4.0.0, so every installation keeps the backend it was given then')
