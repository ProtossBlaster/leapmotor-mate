"""REEV variant in the setup wizard — B10 and C10 get a range-extender battery option,
and selecting it persists is_reev=1 (the gate that later turns on the fuel features). T03
has no REEV variant. Cuts: C10 REEV 28.4 kWh, B10 REEV 18.8 kWh (per published EU specs;
B10 REEV to be confirmed against a real car).

The REEV packs are offered by the BetaTester build ONLY: the official Mate has no REEV support,
so listing the pack would promise something that isn't there. The map holds them for both builds;
the wizard endpoint filters them out unless research mode is on.

Needs web.main (fastapi); the minimal CI env skips this module cleanly.
"""
import asyncio
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")

import json
import re

import db as D
import db_reader
import main
import research


class _Req:
    """Minimal Starlette Request stand-in: setup_submit awaits .form() and reads .headers."""
    def __init__(self, data):
        self._data = data
        self.headers = {}

    async def form(self):
        return self._data


def test_eu_battery_map_offers_reev_for_b10_and_c10():
    reev_c10 = [o for o in main._EU_BATTERY_MAP["C10"] if o.get("reev")]
    reev_b10 = [o for o in main._EU_BATTERY_MAP["B10"] if o.get("reev")]
    assert [o["v"] for o in reev_c10] == ["28.4"]
    assert [o["v"] for o in reev_b10] == ["18.8"]


def test_t03_has_no_reev_variant():
    assert not any(o.get("reev") for o in main._EU_BATTERY_MAP["T03"])


def test_reev_option_does_not_replace_the_bev_variants():
    # The BEV packs must still be selectable — REEV is an *added* option, not a swap.
    assert {"69.9", "81.9"} <= {o["v"] for o in main._EU_BATTERY_MAP["C10"]}
    assert {"55.0", "65.0"} <= {o["v"] for o in main._EU_BATTERY_MAP["B10"]}


class _JsonReq:
    """Minimal Request stand-in for the detect-vehicle endpoint (awaits .json())."""
    def __init__(self, data):
        self._data = data
        self.headers = {}

    async def json(self):
        return self._data


def _detect(monkeypatch, car_type, *, research_on):
    monkeypatch.setattr(research, "research_enabled", lambda: research_on)
    monkeypatch.setattr(main.command_client, "detect_vehicle",
                        lambda u, p, pin: {"car_type": car_type, "vin": "LVIN0000000000001"})
    resp = asyncio.run(main.detect_vehicle_api(
        _JsonReq({"user": "u@example.com", "password": "pw", "pin": "1234"})))
    return json.loads(resp.body)


def test_official_build_never_offers_a_reev_pack(monkeypatch):
    """The crux: Mate has no REEV support, so its wizard must not even mention the pack —
    an owner who picked it would get statistics that quietly don't add up."""
    for car_type in ("C10", "B10"):
        body = _detect(monkeypatch, car_type, research_on=False)
        offered = body.get("battery_options") or []
        assert offered, f"{car_type} must still offer its BEV packs"
        assert not any(o.get("reev") for o in offered)
        assert "28.4" not in {o["v"] for o in offered}
        assert "18.8" not in {o["v"] for o in offered}


def test_betatester_build_offers_reev_and_bev(monkeypatch):
    """MateBetaTesterOnly is where REEV lives — and it must keep offering the BEV packs too,
    since a tester's account may hold either car."""
    body = _detect(monkeypatch, "C10", research_on=True)
    offered = {o["v"] for o in body["battery_options"]}
    assert "28.4" in offered                      # REEV
    assert {"69.9", "81.9"} <= offered            # BEV variants intact


def test_official_build_keeps_the_single_variant_autoset(monkeypatch):
    """T03 has one pack and no REEV → the filter must leave the auto-set path untouched."""
    body = _detect(monkeypatch, "T03", research_on=False)
    assert body["battery_kwh"] == "36.0"
    assert "battery_options" not in body


def _wizard_page(monkeypatch, *, research_on):
    monkeypatch.setattr(research, "research_enabled", lambda: research_on)
    return asyncio.run(main.setup_page(_JsonReq({}))).body.decode()


def _rendered_options(html):
    """The battery map as the wizard page actually ships it to the browser."""
    m = re.search(r"const BATTERY_OPTIONS = (\{.*?\});\s*\n", html, re.S)
    assert m, "the wizard must render the server's battery map"
    return json.loads(m.group(1))


def test_official_wizard_page_ships_no_reev_pack(monkeypatch):
    """The one that would have caught #141: the endpoint's JSON was only half the story — the wizard
    page shipped its OWN hardcoded copy of the list, REEV packs included. Pin the SERVED SOURCE.
    (Match on the parsed map, not on raw substrings: '18.8' also occurs inside an SVG path.)"""
    html = _wizard_page(monkeypatch, research_on=False)
    rendered = _rendered_options(html)
    for car_type, opts in rendered.items():
        assert not any(o.get("reev") for o in opts), car_type
    values = {o["v"] for opts in rendered.values() for o in opts}
    assert "28.4" not in values and "18.8" not in values
    assert {"69.9", "65.0"} <= values                      # the BEV packs are still offered
    assert "REEV" not in html and "range-extender" not in html   # nor as prose/comments


def test_betatester_wizard_page_ships_reev_and_bev(monkeypatch):
    """...and the same page on the beta must still carry both — one image serves both products, so
    a filter that over-reaches would blind the testers instead of protecting Mate's users."""
    rendered = _rendered_options(_wizard_page(monkeypatch, research_on=True))
    values = {o["v"] for opts in rendered.values() for o in opts}
    assert {"28.4", "18.8"} <= values                      # REEV packs
    assert {"69.9", "81.9", "55.0", "65.0"} <= values      # BEV packs


def test_wizard_page_and_endpoint_cannot_drift(monkeypatch):
    """Both render from battery_options_for_build(). They used to be hand-kept copies and had already
    diverged — 56.2/67.1 (gross) in the page vs 55.0/65.0 (usable) from the endpoint."""
    for research_on in (False, True):
        rendered = _rendered_options(_wizard_page(monkeypatch, research_on=research_on))
        served = main.battery_options_for_build()
        assert rendered == served
        body = _detect(monkeypatch, "C10", research_on=research_on)
        assert {o["v"] for o in body["battery_options"]} == {o["v"] for o in served["C10"]}


def _run_setup(tmp_path, monkeypatch, form):
    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    asyncio.run(main.setup_submit(_Req(form)))


_BASE = {"user": "u@example.com", "password": "pw", "pin": "1234", "language": "en",
         "vin": "LVIN0000000000001"}


def test_selecting_reev_persists_flag_and_small_battery(tmp_path, monkeypatch):
    _run_setup(tmp_path, monkeypatch, {**_BASE, "car_type": "C10", "battery": "28.4", "is_reev": "1"})
    assert db_reader.get_setting("is_reev") == "1"
    assert db_reader.get_setting("battery_capacity_kwh") == "28.4"


def test_selecting_a_bev_pack_leaves_is_reev_off(tmp_path, monkeypatch):
    _run_setup(tmp_path, monkeypatch, {**_BASE, "car_type": "B10", "battery": "67.1", "is_reev": "0"})
    assert db_reader.get_setting("is_reev") == "0"


def test_missing_is_reev_defaults_off(tmp_path, monkeypatch):
    # An old client that doesn't send the field must not accidentally flag the car as REEV.
    _run_setup(tmp_path, monkeypatch, {**_BASE, "car_type": "B10", "battery": "65.0"})
    assert db_reader.get_setting("is_reev") == "0"
