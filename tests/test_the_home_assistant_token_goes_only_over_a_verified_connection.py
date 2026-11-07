"""The Home Assistant token is sent only over a verified connection to a public name.

ha_client turned certificate checks off for every https URL, because a Home Assistant on the LAN
often has a self-signed certificate. The token it sends is a long-lived Home Assistant token, an
administrator's key to the house. A URL on a public name (Nabu Casa, a DuckDNS proxy) has a real
certificate, and there the unchecked connection let anyone on the path read the token. A private
address or a home-network name keeps the tolerance it had.
"""
import ssl

import pytest

import ha_client


@pytest.mark.parametrize("url", [
    "https://abcdef.ui.nabu.casa", "https://myhome.duckdns.org:8123", "https://8.8.8.8:8123",
])
def test_a_public_home_assistant_is_verified(url):
    ctx = ha_client._tls_context(url)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


@pytest.mark.parametrize("url", [
    "https://192.168.1.10:8123", "https://homeassistant.local:8123", "https://homeassistant:8123",
    "https://[fd00::10]:8123", "https://ha.home.arpa",
])
def test_a_home_assistant_on_the_lan_may_keep_a_self_signed_certificate(url):
    ctx = ha_client._tls_context(url)
    assert ctx.verify_mode == ssl.CERT_NONE and not ctx.check_hostname
