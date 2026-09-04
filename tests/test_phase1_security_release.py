"""Regression proofs for the security-only release path."""
from pathlib import Path
import re

import pytest

pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

from starlette.testclient import TestClient

import main


ROOT = Path(__file__).resolve().parents[1]


def test_a_crafted_host_cannot_turn_an_api_route_into_a_public_static_path(monkeypatch):
    monkeypatch.setenv("MATE_AUTH_PASSWORD", "correct-horse")
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)

    response = TestClient(main.app).post(
        "/api/settings/language",
        headers={"Host": "attacker.invalid/static/?x="},
        data={"language": "fr"},
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_matedesktop_listens_only_on_loopback(monkeypatch):
    monkeypatch.setenv("MATE_DESKTOP", "1")
    assert hasattr(main, "_web_listen_host"), "the web host is not selected per deployment"
    assert main._web_listen_host() == "127.0.0.1"


@pytest.mark.parametrize("desktop_value", [None, "", "0"])
def test_docker_and_addon_keep_listening_on_all_interfaces(monkeypatch, desktop_value):
    if desktop_value is None:
        monkeypatch.delenv("MATE_DESKTOP", raising=False)
    else:
        monkeypatch.setenv("MATE_DESKTOP", desktop_value)
    assert hasattr(main, "_web_listen_host"), "the web host is not selected per deployment"
    assert main._web_listen_host() == "0.0.0.0"


def test_ci_installs_the_same_dependency_sets_as_production():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "pip install pytest httpx -r poller/requirements.txt -r web/requirements.txt" in workflow


def test_release_version_is_consistent_across_runtime_image_changelog_and_manuals():
    version = main.MATE_VERSION
    dockerfile = (ROOT / "Dockerfile").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()
    manuals = (
        ROOT / "docs" / "USER-MANUAL-EN.md",
        ROOT / "docs" / "MANUALE-UTENTE-IT.md",
        ROOT / "docs" / "MANUEL-UTILISATEUR-FR.md",
        ROOT / "docs" / "BENUTZERHANDBUCH-DE.md",
        ROOT / "docs" / "MANUAL-DE-USUARIO-ES.md",
    )

    assert f'io.hass.version="{version}"' in dockerfile
    assert re.search(r"^## \[" + re.escape(version) + r"\]", changelog, re.MULTILINE)
    for manual in manuals:
        assert f"v{version}" in "\n".join(manual.read_text().splitlines()[:5]), manual.name
