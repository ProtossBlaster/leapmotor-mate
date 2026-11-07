"""A page on someone else's domain cannot drive a Mate that has no password, by DNS rebinding either.

The origin guard compares the page's origin with the Host the request arrived on. A page on
evil.com that gets its own name to resolve to the owner's Mate (DNS rebinding) sends both as
evil.com: they match, and the guard let the write through. Measured on 4.24.0, 07/11/2026: a
POST with Host and Origin evil.com:4099 changed a setting (204, the value in the database),
and a GET read Mate's data back. With no password that is the whole API, /api/command/unlock
included.

A rebinding attacker needs a name of their own, a public one. So a Mate with no password answers
only to the names a home network uses (an IP address, localhost, a single-label name, .local, .lan,
.home, .home.arpa, .internal, .localdomain) and to the ones named in MATE_ALLOWED_HOSTS or
MATE_TRUSTED_ORIGINS. With a password the session cookie is bound to the real name and a rebound
page never has it, so nothing changes there.

And the add-on: its guards were off for every request, because Home Assistant's ingress
authenticates. A request that did not come through ingress (the Supervisor's proxy, 172.30.32.2)
reached the same API with none of them when the owner opened port 4000.
"""
import pytest

import security

pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

from starlette.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.fixture
def standalone(monkeypatch):
    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN", "MATE_ALLOWED_HOSTS",
                "MATE_TRUSTED_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(main.auth, "_stored", lambda: "")


@pytest.mark.parametrize("host", [
    "192.168.1.50:4000", "10.0.0.7", "[::1]:4000", "[fd00::5]:4000", "localhost:4000",
    "nas:4000", "homeassistant.local:4000", "mate.home.arpa", "mate.lan", "box.internal",
    "nas.local.", "local-leapmotor-mate:4000", "mate.test", "",
])
def test_a_home_network_name_is_answered(standalone, host):
    assert security.host_allowed(host) is True


@pytest.mark.parametrize("host", [
    "evil.com:4000", "rebind.attacker.com", "192.168.1.50.nip.io:4000", "mate.mydomain.net",
])
def test_a_public_name_is_not_answered_without_a_password(standalone, host):
    assert security.host_allowed(host) is False


def test_a_public_name_can_be_named_by_the_owner(standalone, monkeypatch):
    monkeypatch.setenv("MATE_ALLOWED_HOSTS", "mate.mydomain.net, other.mydomain.net:8443")
    assert security.host_allowed("mate.mydomain.net") is True
    assert security.host_allowed("mate.mydomain.net:443") is True
    assert security.host_allowed("other.mydomain.net:8443") is True
    assert security.host_allowed("evil.com") is False


def test_a_trusted_origin_is_also_an_allowed_name(standalone, monkeypatch):
    monkeypatch.setenv("MATE_TRUSTED_ORIGINS", "https://mate.mydomain.net")
    assert security.host_allowed("mate.mydomain.net") is True


def test_a_rebound_page_cannot_write(standalone):
    response = TestClient(main.app).post(
        "/api/settings/currency", data={"currency": "USD"},
        headers={"Host": "evil.com:4000", "Origin": "http://evil.com:4000"},
        follow_redirects=False)
    assert response.status_code == 403


def test_a_rebound_page_cannot_read(standalone):
    response = TestClient(main.app).get(
        "/api/nav/current-address", headers={"Host": "evil.com:4000"}, follow_redirects=False)
    assert response.status_code == 403


def test_the_owner_on_the_lan_still_gets_through(standalone):
    response = TestClient(main.app).get("/healthz", headers={"Host": "192.168.1.50:4000"})
    assert response.status_code != 403
    response = TestClient(main.app).post(
        "/api/settings/currency", data={"currency": "EUR"},
        headers={"Host": "192.168.1.50:4000", "Origin": "http://192.168.1.50:4000"},
        follow_redirects=False)
    assert response.status_code != 403


def test_with_a_password_a_public_name_is_left_to_the_login(standalone, monkeypatch):
    """A reverse proxy on a public name with a password set keeps working: the login answers."""
    monkeypatch.setenv("MATE_AUTH_PASSWORD", "correct-horse")
    response = TestClient(main.app).post(
        "/api/settings/currency", data={"currency": "USD"},
        headers={"Host": "mate.mydomain.net", "Origin": "https://mate.mydomain.net"},
        follow_redirects=False)
    assert response.status_code == 401


def test_the_add_on_port_opened_outside_ingress_is_guarded(standalone, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    client = TestClient(main.app, client=("192.168.1.20", 50000))
    response = client.post("/api/settings/currency", data={"currency": "USD"},
                           headers={"Host": "192.168.1.10:4000", "Origin": "https://evil.com"},
                           follow_redirects=False)
    assert response.status_code == 403
    page = client.get("/healthz", headers={"Host": "192.168.1.10:4000"})
    assert page.headers.get("x-frame-options") == "DENY"


def test_ingress_itself_is_unchanged(standalone, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    client = TestClient(main.app, client=(security.INGRESS_PROXY, 50000))
    response = client.post("/api/settings/currency", data={"currency": "EUR"},
                           headers={"Host": "a0d7b954-leapmotor-mate:4000",
                                    "Origin": "https://ha.mydomain.net"},
                           follow_redirects=False)
    assert response.status_code != 403
    page = client.get("/healthz")
    assert "x-frame-options" not in page.headers


def test_the_add_on_refusal_points_at_the_panel(standalone, monkeypatch):
    """An add-on has no password and no environment to set: the refusal names what works there."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    response = TestClient(main.app, client=("192.168.1.20", 50000)).get(
        "/api/nav/current-address", headers={"Host": "mate.mydomain.net:4000"})
    assert response.status_code == 403
    assert "Home Assistant" in response.text
    assert "MATE_ALLOWED_HOSTS" not in response.text
