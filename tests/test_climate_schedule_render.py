"""Render guard for partials/climate_schedule.html (the climate-schedule EDITOR).

Since 1.11.8 the climate schedule is WRITABLE and this partial is an interactive editor: the
current schedule is loaded client-side via GET /api/climate-schedule (not server-rendered). This
guards that the editor template renders without error and keeps its load-bearing structure — the
five preset radio buttons, the form target, the temperature slider, the day chips, the i18n labels
— and that every MDI preset icon resolves to a non-empty SVG path (a typo'd icon name would render
an empty ``<path d="">``).

Skipped where jinja2 isn't installed (the CI test env per pytest.ini)."""
import pathlib

import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"


def _render():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    return env.get_template("partials/climate_schedule.html").render(t=lambda k: k)


def test_editor_renders_presets_form_and_controls():
    out = _render()
    assert 'hx-post="api/climate-schedule"' in out             # posts to the climate endpoint
    for p in ("cool", "heat", "vent", "defrost", "none"):      # all five quick presets present
        assert f'id="cls-preset-{p}"' in out and f'value="{p}"' in out
    assert 'type="range" name="temperature"' in out            # temperature slider
    assert 'id="cls-day-0"' in out and 'id="cls-day-6"' in out  # day chips (0=Sun..6=Sat)
    assert 'id="cls-enabled"' in out                           # active master toggle


def test_preset_mdi_icons_resolve():
    out = _render()
    assert "<svg" in out                                       # MDI icons render
    assert 'd=""' not in out                                   # every ico.mdi(name) resolved (no empty path)


def test_i18n_labels_referenced():
    out = _render()
    for k in ("sched_climate_title", "sched_clima_cool", "sched_clima_heat",
              "sched_clima_vent", "sched_clima_defrost", "sched_clima_none", "sched_temp"):
        assert k in out
