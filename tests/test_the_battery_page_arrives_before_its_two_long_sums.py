"""The Battery page must arrive; its two long sums can follow.

Measured on the production add-on (aarch64) on 28/09/2026: **/battery 3.526 s**. It is two figures,
and on the lab against a real database they are

    get_vampire_drain   498.9 ms      get_battery_health   216.9 ms

which on that hardware is roughly 2.0 s and 0.9 s. Neither is a query that can be indexed away:
the drain reads ninety days of position rows — about 250 000 of them — and groups consecutive
samples into parks, and the health estimate integrates the power samples of every qualifying
charge. Both are honest work over data that only grows.

So the page stops waiting for them, the way Settings stopped waiting for the capacity hint and the
Cloud link card stopped being built for every load: the shell arrives, and each section fetches
itself. The arithmetic is untouched — what changes is that nothing blocks on it.
"""
import inspect
import pathlib

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"


def test_the_page_itself_computes_neither():
    import main
    source = inspect.getsource(main.battery_page)
    for heavy in ("get_battery_health", "get_vampire_drain"):
        assert heavy not in source, f"the page still waits for {heavy}"


def test_each_section_has_an_endpoint():
    import main
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    for path in ("/api/battery-health", "/api/battery-vampire"):
        assert path in paths, f"{path} missing: {sorted(p for p in paths if 'battery' in p)}"


def test_the_page_fetches_both_and_renders_neither_inline():
    html = (TEMPLATES / "battery.html").read_text(encoding="utf-8")
    assert "api/battery-health" in html and "api/battery-vampire" in html
    assert "health.latest_soh_pct" not in html, "the health section is still inline"
    assert "vampire.measurable_count" not in html, "the drain section is still inline"


def test_the_sections_still_say_what_they_said():
    """Moved, not rewritten: the figures and the notes are the same ones."""
    health = (TEMPLATES / "partials" / "battery_health.html").read_text(encoding="utf-8")
    vampire = (TEMPLATES / "partials" / "battery_vampire.html").read_text(encoding="utf-8")
    for needle in ("health.latest_soh_pct", "battery_est_capacity", "battery_samples",
                   "battery_soh_cold_note"):
        assert needle in health, f"{needle} lost from the health section"
    for needle in ("vampire.measurable_count", "battery_vampire_typical", "day_unit"):
        assert needle in vampire, f"{needle} lost from the drain section"
