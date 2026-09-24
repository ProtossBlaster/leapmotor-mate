"""The cadence sliders reach every value the form will accept.

Noticed by @arekm while writing PR #310 and left alone there, correctly: it is not his change.

`db_reader.POLL_PARKED_RANGE_S` is documented as *the range the settings form allows*, and the
handler clamps to it — 10 to 600 seconds. The slider that IS that form stopped at **300**. So the
constant made a claim about the form that the form did not keep: a value between 301 and 600 is
accepted if something else writes it (an import, a hand-edited row, an older page), is honoured by
the poller, and then cannot be reproduced or even represented by the control that is supposed to
own it — drag the slider once and it is silently halved.

The slider is raised to meet the constant rather than the constant lowered to meet the slider: a
lower ceiling would re-clamp a stored 600 the next time anyone touched that page, which is a
setting changing itself behind its owner.
"""
import pathlib
import re

import db_reader

FORM = (pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
        / "settings.html").read_text(encoding="utf-8")


def _slider(name: str) -> dict:
    m = re.search(rf'<input type="range" name="{name}"[^>]*>', FORM)
    assert m, f"the {name} slider is gone from Settings"
    tag = m.group(0)
    return {k: float(v) for k, v in re.findall(r'(min|max|step)="([0-9.]+)"', tag)}


def test_the_parked_slider_reaches_the_whole_accepted_range():
    lo, hi = db_reader.POLL_PARKED_RANGE_S
    s = _slider("poll_parked")
    assert (s["min"], s["max"]) == (float(lo), float(hi)), \
        f"the form accepts {lo}-{hi}s, the slider offers {s['min']:.0f}-{s['max']:.0f}s"


def test_the_driving_slider_reaches_the_whole_accepted_range():
    lo, hi = db_reader.POLL_DRIVING_RANGE_S
    s = _slider("poll_driving")
    assert (s["min"], s["max"]) == (float(lo), float(hi))


def test_the_defaults_sit_inside_their_own_slider():
    """A default the control cannot represent would move the moment the page is saved."""
    for name, default, (lo, hi) in (
            ("poll_parked", db_reader.POLL_PARKED_DEFAULT_S, db_reader.POLL_PARKED_RANGE_S),
            ("poll_driving", db_reader.POLL_DRIVING_DEFAULT_S, db_reader.POLL_DRIVING_RANGE_S)):
        s = _slider(name)
        assert lo <= default <= hi
        assert s["min"] <= default <= s["max"]
        assert (default - s["min"]) % s["step"] == 0, \
            f"{name}: the default {default}s is not on a step of its slider"
