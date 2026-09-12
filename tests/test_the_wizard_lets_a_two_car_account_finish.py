"""Two cars on one account, a pack chosen on each, and the wizard has to accept them (#280).

`validateSubmit()` read one field, `h-battery`. The multi-car branch never fills it: it draws a
card per car, writes the answers into `h-vehicles` and returns before the single-car battery block
runs. So from v3.13.0 — the release that added the per-car cards — every account with two or more
cars was answered "Please select a battery variant first." on a form where every pack *was*
selected, and there was no way past it.

Nothing saw it: the server side is tested (`test_setup_configures_every_car.py` covers
`apply_setup_vehicles`), the cards are tested, and the gate between them was a line of browser
JavaScript nobody ran. So this file runs it — the real script out of the real template, in node.
"""
import pathlib
import shutil
import subprocess

import jinja2
import pytest

import battery_packs

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "web" / "templates" / "setup.html"


class _Request:
    """Enough of a request for the `<base href>` line; the wizard reads nothing else off it."""
    headers: dict[str, str] = {}


def _rendered_script(tmp_path: pathlib.Path) -> pathlib.Path:
    """The page's own script, Jinja-rendered exactly as the browser receives it."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ROOT / "web" / "templates")))
    html = env.get_template("setup.html").render(
        request=_Request(),
        # The official build's list: the range-extender packs are filtered out server-side.
        battery_options={ct: [o for o in opts if not o.get("reev")]
                         for ct, opts in battery_packs.EU_BATTERY_MAP.items()},
        research=False,
        tz_options=[], tz_detected="Europe/Rome", prefill=None,
    )
    body = html[html.rindex("<script>") + len("<script>"):html.rindex("</script>")]
    assert "function validateSubmit" in body, "the wizard's script is not where this test looks"
    out = tmp_path / "setup_wizard.js"
    out.write_text(body)
    return out


def test_the_wizard_accepts_two_cars_and_still_refuses_an_empty_form(tmp_path):
    """Five scenarios, run in node against the real script — see setup_wizard_two_cars.cjs."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required to run the wizard's own script")
    subprocess.run([node, str(ROOT / "tests/setup_wizard_two_cars.cjs"),
                    str(_rendered_script(tmp_path))], check=True)


def test_the_guard_reads_the_field_the_multi_car_form_posts():
    """Runs with or without node: the guard must consult `h-vehicles`, which is what a two-car
    wizard submits. Reading only `h-battery` is the #280 bug, and it is invisible from Python."""
    src = SETUP.read_text()
    body = src[src.index("function validateSubmit"):]
    body = body[:body.index("\n}") + 2]
    assert "h-vehicles" in body, "validateSubmit() still ignores the multi-car field"
