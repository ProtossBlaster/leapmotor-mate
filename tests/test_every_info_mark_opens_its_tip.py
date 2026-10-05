"""An ⓘ on a page exists for its tip, so every one opens the app's own tooltip (data-tip, base.html)
on hover, on tap and on keyboard focus. A mark that carries only the browser's `title` is one
most readers never get to open: that tip needs a second of hovering, never shows on touch and not
at all inside the Home Assistant app. The mark's tip is an attribute of its template, which is
why the templates are read here rather than every page rendered.
"""
import pathlib
import re

import pytest

_TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "web" / "templates"
_TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^>]*)>")
_VOID = {"br", "hr", "img", "input", "link", "meta", "source", "wbr"}
_COMMENT = re.compile(r"<!--.*?-->|\{#.*?#\}", re.DOTALL)


def _host_attrs(before: str) -> str:
    """The attributes of the innermost element still open where the text ends."""
    stack = []
    for closing, name, attrs in _TAG.findall(before):
        if closing:
            while stack and stack.pop()[0] != name:
                pass
        elif name not in _VOID and not attrs.rstrip().endswith("/"):
            stack.append((name, attrs))
    return stack[-1][1] if stack else ""


def _info_marks():
    for path in sorted(_TEMPLATES.rglob("*.html")):
        text = _COMMENT.sub("", path.read_text(encoding="utf-8"))
        for m in re.finditer("ⓘ", text):
            line = text.count("\n", 0, m.start()) + 1
            yield pytest.param(_host_attrs(text[:m.start()]), id=f"{path.relative_to(_TEMPLATES)}:{line}")


@pytest.mark.parametrize("attrs", list(_info_marks()))
def test_every_info_mark_opens_the_apps_tip(attrs):
    assert 'data-tip="' in attrs, "the mark shows nothing on tap or in the Home Assistant app"
    assert 'tabindex="0"' in attrs, "the mark cannot be reached from the keyboard"

