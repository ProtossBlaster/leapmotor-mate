"""Typing a station name must not require the car to have known where it was (#197 @adoewa).

The ✏️ free-text name shipped in v2.17.0 (PR #194, discussion #193). It was added to the END of a
block that had been there since the charger-locator work, and that block opens with

    {% if charge.location_type != 'HOME' and charge.latitude and charge.longitude %}

The coordinates belong to the 🔄 lookup, which searches OSM/OCM *around a point* and genuinely
cannot run without one. The ✏️ is free text and never needed them — it just inherited the guard,
seven lines above the diff's context, where no reviewer would see it.

A charge entered by hand has no coordinates by construction (that is the very test `manual_entry`
uses to recognise one), so the whole block was skipped and the pencil never appeared. @adoewa found
it with the only thing that could: older charges he had typed in himself.

These render the real template, because a fake renderer cannot catch a gate that lives in Jinja.
"""
import pathlib

import jinja2
import pytest


@pytest.fixture
def render():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(
            str(pathlib.Path(__file__).resolve().parent.parent / "web" / "templates")),
        autoescape=True)
    tpl = env.get_template("partials/charge_location.html")

    def _r(**charge):
        base = {"id": 7, "location_type": "FAST", "latitude": None, "longitude": None,
                "location_name": None, "location_url": None}
        base.update(charge)
        return tpl.render(charge=base, t=lambda k, **kw: k, error=None, not_found=None)
    return _r


def _has_pencil(html):
    return "locate/manual" in html


def _has_lookup(html):
    return 'hx-post="api/charges/7/locate"' in html


# ── the defect ─────────────────────────────────────────────────────────────────

def test_a_hand_entered_charge_can_still_be_given_a_station_name(render):
    """No coordinates — the case @adoewa reported."""
    html = render(latitude=None, longitude=None)
    assert _has_pencil(html), "no ✏️ on a charge the user typed in himself"


def test_the_lookup_stays_hidden_without_coordinates(render):
    """🔄 searches around a point. Without one there is nothing to search around, so offering the
    button would only produce a failure the user cannot act on."""
    html = render(latitude=None, longitude=None)
    assert not _has_lookup(html)


# ── everything else must be exactly as it was ──────────────────────────────────

def test_a_located_charge_offers_both(render):
    html = render(latitude=45.44, longitude=9.12)
    assert _has_pencil(html) and _has_lookup(html)


def test_home_offers_neither(render):
    """Naming your own wallbox as a public station means nothing, with or without a fix."""
    assert not _has_pencil(render(location_type="HOME", latitude=45.44, longitude=9.12))
    assert not _has_lookup(render(location_type="HOME", latitude=45.44, longitude=9.12))
    assert not _has_pencil(render(location_type="HOME"))


def test_a_zero_zero_fix_counts_as_no_fix(render):
    """A charge the car reported with no GPS is stored 0,0 — not NULL. The falsy guard has always
    been the point (see the charges Null Island case); it must keep the lookup away while still
    letting the name through."""
    html = render(latitude=0.0, longitude=0.0)
    assert not _has_lookup(html)
    assert _has_pencil(html)


def test_the_name_already_saved_comes_back_into_the_field(render):
    html = render(location_name="Ionity Brescia Est")
    assert "Ionity Brescia Est" in html
