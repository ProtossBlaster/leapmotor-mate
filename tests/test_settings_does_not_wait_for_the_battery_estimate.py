"""Opening Settings must not wait while the pack's measured capacity is worked out.

Measured on the production add-on (aarch64) on 28/09/2026, inside the container:

    /settings  1.356 s      get_battery_health  927.3 ms

Settings pays all of that for ONE number — the "measured: xx.x kWh" hint under the capacity field,
with a button that fills the field in. `get_battery_health` integrates the power samples of every
qualifying charge, one query per charge and then some (178 queries on a real database), and the
page cannot appear until it is done.

So the hint fetches itself, the way the Cloud link card does since 4.5.1. The figure is unchanged
and so is the button; what changes is that nobody waits for it to read the rest of the page.
"""
import inspect
import pathlib

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_settings_page_does_not_build_the_battery_estimate():
    import main
    source = inspect.getsource(main.settings_page)
    assert "get_battery_health" not in source, (
        "Settings still waits for the battery estimate; it belongs to the hint's own fetch"
    )


def test_the_hint_has_an_endpoint_of_its_own():
    import main
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    assert "/api/measured-capacity" in paths, sorted(p for p in paths if "capacity" in p)


def test_the_template_fetches_the_hint():
    html = (ROOT / "web/templates/settings.html").read_text(encoding="utf-8")
    assert "api/measured-capacity" in html, "the hint is not fetched"
    assert "{% if measured_capacity %}" not in html, (
        "the hint is still rendered inline, so the page still waits for it"
    )


def test_the_hint_still_offers_the_figure_and_the_button():
    """What the user sees must not change — only when it arrives."""
    partial = (ROOT / "web/templates/partials/measured_capacity.html").read_text(encoding="utf-8")
    assert "capacity_measured" in partial and "capacity_use_measured" in partial, partial[:200]
    assert "battery_capacity_kwh.value" in partial, "the button no longer fills the field"
