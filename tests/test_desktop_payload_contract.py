"""A new import here can kill MateDesktop on somebody's Mac. This is where that gets caught.

MateDesktop is a shell plus a payload. The shell carries Python and the libraries and is signed and
released rarely; the payload is `web/` and `poller/` — this code — and it flows to every installed
copy on **every Mate tag**. So the shell stays frozen at what it was built with while the code
running inside it moves on.

Which means an import added here can land on a shell that has no such module, and the app dies at
startup. MateDesktop's updater does guard against that, but it reads the payload's
`requirements.txt`: it catches a new *package* and is blind to a new *module of the standard
library*, which appears in no requirements file. And the shell does not carry the whole stdlib —
`statistics`, for one, is not in PyInstaller's `base_library.zip`; it is in the app only because the
build happened to see it used.

`mate_desktop/payload_deps.py` is the contract that makes the build see it. This test compares that
contract against what this repository actually imports, so the mismatch shows up **before a tag**
rather than on a user's machine.

Real case: Mate 3.4.10 started importing PIL directly (`car_image.py`, measuring which way the
charging animation runs) and the contract never learned. It worked anyway — Pillow arrives as an
extra of `leapmotor-api[image]` — which is precisely the kind of luck that runs out quietly.

Needs the MateDesktop checkout next door; CI has neither, so it skips there. That is not the usual
"skipped where it matters" trap: this check can only exist where both repositories do, and that is
the machine where releases are cut.
"""
import ast
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESKTOP = pathlib.Path(os.environ.get("MATE_DESKTOP_REPO", pathlib.Path.home() / "mate-desktop"))
CONTRACT = DESKTOP / "mate_desktop" / "payload_deps.py"

# Imported by the shell itself, never by the payload.
SHELL_ONLY = {"mate_desktop", "webview", "PyInstaller"}


def _top_level_imports(path: pathlib.Path) -> set[str]:
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.add(n.module.split(".")[0])
    return out


@pytest.mark.skipif(not CONTRACT.is_file(),
                    reason=f"MateDesktop checkout not found at {DESKTOP} — set MATE_DESKTOP_REPO")
def test_no_import_here_is_missing_from_the_desktop_shell_contract():
    files = [p for d in ("web", "poller") for p in (ROOT / d).rglob("*.py")]
    local = {p.stem for p in files}                       # the payload's own modules
    used: set[str] = set()
    for p in files:
        used |= _top_level_imports(p)

    # Built-ins are compiled into the interpreter itself (`sys`, `time`, …): they cannot be absent
    # from a frozen build, so declaring them would be noise.
    missing = sorted(m for m in used - _top_level_imports(CONTRACT) - local - SHELL_ONLY
                     if m not in sys.builtin_module_names)
    assert not missing, (
        "these are imported here and NOT declared in MateDesktop's payload_deps.py:\n  "
        + "\n  ".join(missing)
        + "\n\nA MateDesktop shell built without them ships without them, and the payload dies at "
          "startup on every desktop install. Add them to "
          f"{CONTRACT}, and rebuild the shell BEFORE this goes out as a tag."
    )
