"""A refused password longer than the Leapmotor app can keep gets a hint in the setup wizard.

The app's password field can keep the first 16 characters and drop the rest without a warning, so a
longer password pasted into it is not the one the account has. Mate sends what is typed, whole, and
the cloud refuses it with 302010108 (#482). After *Detect my car* is refused that way with a longer
password, the wizard says so; any other failure, or a password of 16 characters or fewer, shows the
error as before.
"""
import pathlib
import shutil
import subprocess

import pytest
from test_a_refused_password_is_named_by_its_code import _refused, api  # noqa: F401 — fixture
from test_the_wizard_lets_a_two_car_account_finish import _rendered_script

import command_client

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _detect_after(monkeypatch, error):
    class RefusingApi:
        def __init__(self, **kw): pass
        def login(self): raise error
    monkeypatch.setattr(command_client, "LeapmotorApiClient", RefusingApi)
    return command_client.detect_vehicle("synthetic-user", "synthetic-password", "1234")


def test_a_detection_the_cloud_refuses_for_the_password_says_so(api, monkeypatch):  # noqa: F811
    assert _detect_after(monkeypatch, _refused(api, monkeypatch, 302010108))["password_refused"] is True


def test_a_detection_refused_for_another_reason_does_not(api, monkeypatch):  # noqa: F811
    assert _detect_after(monkeypatch, _refused(api, monkeypatch, 302010219))["password_refused"] is False


def test_the_wizard_hints_at_the_app_s_cut_only_for_a_longer_refused_password(tmp_path):
    """Run in node against the real script: see setup_wizard_detection_fails.cjs."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required to run the wizard's own script")
    subprocess.run([node, str(ROOT / "tests/setup_wizard_detection_fails.cjs"),
                    str(_rendered_script(tmp_path)), "cut"], check=True)
