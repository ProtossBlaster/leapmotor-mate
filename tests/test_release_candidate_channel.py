import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('release_channel',Path(__file__).resolve().parents[1]/'.github/scripts/release_channel.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

@pytest.mark.parametrize('version',['4.0.0-rc.1','4.0.0-beta.2','4.0.0-alpha.1'])
def test_candidates_do_not_advance_stable_channel(version):assert module.prerelease(version)

def test_final_release_can_advance_stable_channel():assert not module.prerelease('4.0.0')

@pytest.mark.parametrize('version',['4','latest','4.0.0; echo bad'])
def test_ambiguous_versions_are_rejected(version):
    with pytest.raises(ValueError):module.prerelease(version)
