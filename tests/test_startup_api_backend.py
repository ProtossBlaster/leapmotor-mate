"""Backend selection is process-wide and precedes clients; no request-time fallback."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'poller' / 'mate_api_runtime'


def _run(code, tmp_path, flag=None):
    env = dict(os.environ, DB_PATH=str(tmp_path / 'absent' / 'mate.db'))
    env.pop('MATE_API_V2', None)
    if flag is not None:
        env['MATE_API_V2'] = flag
    result = subprocess.run([sys.executable, '-c', code], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('flag, expected', [(None, 'new'), ('1', 'new'), ('0', 'legacy')])
def test_selection_requires_explicit_legacy_decision(tmp_path, flag, expected):
    _run(f'''
import os, sys, types
sys.path.insert(0, {str(RUNTIME)!r})
class Legacy: pass
class New: pass
sys.modules['leapmotor_api'] = types.SimpleNamespace(LeapmotorApiClient=Legacy)
sys.modules['api_v2_bridge'] = types.SimpleNamespace(NewAPIClient=New)
import api_backend
assert api_backend.LeapmotorApiClient is {{'new': New, 'legacy': Legacy}}[{expected!r}]
# No runtime mutation/retry can switch the already-selected constructor.
os.environ['MATE_API_V2'] = '1' if {expected!r} == 'legacy' else '0'
assert api_backend.LeapmotorApiClient is {{'new': New, 'legacy': Legacy}}[{expected!r}]
''', tmp_path, flag)


@pytest.mark.parametrize('surface', ['web', 'poller'])
@pytest.mark.parametrize('flag', ['0', '1'])
def test_activation_precedes_backend_import_on_both_surfaces(tmp_path, surface, flag):
    _run(f'''
import os, sys, types
sys.path.insert(0, {str(ROOT / surface)!r})
steps = []
sys.modules['runtime_paths'] = types.SimpleNamespace(configure=lambda: steps.append('configure'))
def activate():
    assert steps == ['configure']
    steps.append('activate')
    os.environ['MATE_API_V2'] = {flag!r}
    return {{'backend': 'legacy' if {flag!r} == '0' else 'new'}}
sys.modules['migration_activation'] = types.SimpleNamespace(activate_installation=activate)
class Legacy: pass
class New: pass
sys.modules['leapmotor_api'] = types.SimpleNamespace(LeapmotorApiClient=Legacy)
sys.modules['api_v2_bridge'] = types.SimpleNamespace(NewAPIClient=New)
import mate_api
import api_backend
assert steps == ['configure', 'activate']
assert api_backend.LeapmotorApiClient is (Legacy if {flag!r} == '0' else New)
''', tmp_path)


def test_legacy_start_does_not_prepare_material_or_start_independent_history(tmp_path):
    _run(f'''
import os, sys
from pathlib import Path
sys.path.insert(0, {str(RUNTIME)!r})
import runtime_paths, history_service
assert runtime_paths.prepare_installation() == {{'state': 'legacy'}}
assert not Path(os.environ['DB_PATH']).parent.exists()
history_service.start_history_worker()
assert history_service._thread is None
assert 'history_worker' not in sys.modules
assert 'bootstrap_independent' not in sys.modules
assert 'migration_state' not in sys.modules
''', tmp_path, '0')
