"""#276 (@joeyoong): the "+ Log" button on every Maintenance card did nothing.

Since the page was born (v1.22.0) that button was a `<details>` holding only its own `<summary>`: a
click opened an empty box. The form that records a service sat further down the card, behind a
second, grey "✎ Mark this service as done". He clicked the visible one, saw nothing, and concluded
the log did not work.

Each card now has ONE log control: the header button, which opens that card's form. These tests
render the real template over the real B10 service pack and check the wiring — which element the
button opens, and what that element contains — not the wording.
"""
import re

import jinja2
import pytest

import maintenance
import units


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(maintenance, "get_baseline", lambda: ("2025-11-01", 0.0, True))
    monkeypatch.setattr(maintenance, "latest_logs", lambda vid: {})
    monkeypatch.setattr(units, "get_unit_system", lambda: "metric")
    maint = maintenance.compute({"car_type": "B10", "id": 1}, 11563, "en")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader("web/templates"))
    out = env.get_template("partials/maintenance_content.html").render(
        maint=maint, m=maintenance.chrome("en"), today="2026-09-11")
    return out, maint


def _log_buttons(out):
    return re.findall(r"<button\b[^>]*>\s*\+ Log\s*</button>", out)


def test_no_card_has_an_empty_disclosure(page):
    out, _ = page
    empty = re.findall(r"<details\b[^>]*>\s*<summary\b[^>]*>.*?</summary>\s*</details>", out, re.S)
    assert not empty, f"a <details> with nothing but its <summary> opens an empty box: {empty[0][:160]}"


def test_every_log_button_opens_its_own_form(page):
    out, maint = page
    buttons = _log_buttons(out)
    assert len(buttons) == len(maint["rows"]) > 0, "one log button per service card"
    targets = [re.search(r'aria-controls="([^"]+)"', b).group(1) for b in buttons]
    assert len(set(targets)) == len(targets), "two buttons opening the same form"
    for t in targets:
        start = out.index(f'id="{t}"')
        form = re.search(r"<form\b[^>]*>", out[start:])
        assert form and 'hx-post="api/maintenance/log"' in form.group(0), \
            f"#{t} must hold the form that records the service"
        nxt = out.find('id="maint-log-', start + 1)          # the form is THIS card's, not the next one's
        assert nxt == -1 or start + form.start() < nxt


def test_the_form_starts_closed_and_the_button_says_so(page):
    out, _ = page
    buttons = _log_buttons(out)
    assert buttons, "no log button to check — an empty loop proves nothing"
    for b in buttons:
        t = re.search(r'aria-controls="([^"]+)"', b).group(1)
        opening = re.search(rf'<div\b[^>]*id="{t}"[^>]*>', out).group(0)
        assert re.search(r'class="[^"]*\bhidden\b', opening), "the card must look as before until clicked"
        assert 'aria-expanded="false"' in b


def test_the_second_grey_control_is_gone(page):
    out, maint = page
    assert "Mark this service as done" not in re.sub(r'title="[^"]*"', "", out), \
        "one control per card: the grey duplicate is what hid the working form"
    assert out.count('hx-post="api/maintenance/log"') == len(maint["rows"])
