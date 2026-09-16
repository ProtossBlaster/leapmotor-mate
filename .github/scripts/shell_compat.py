"""Would the published desktop shell accept this payload? Ask ITS OWN guard, not a proxy.

WHY THIS EXISTS IN THIS SHAPE. The check that stood here until 16/09/2026 asked a different,
cruder question: `git diff` on the two requirements.txt between HEAD and the commit the shell was
built from. Any change at all was an error on a tag. That is not what the app does, and the gap is
not academic — it made this job RED on four consecutive releases (v3.15.16 → v3.16.0) while the
published shell was in fact perfectly happy, and the cost of crying wolf is that nobody reads the
tag's CI any more, including on the day it is finally right.

WHAT THE APP ACTUALLY ASKS (mate_desktop/updater.py:unsatisfied_requirements). Not "did the file
change" but "can THIS interpreter satisfy every line": a `==`/`~=` pin that disagrees with what is
installed blocks, a `>=` floor that is not met blocks, and a distribution with no metadata blocks
only when it cannot even be imported. Upper bounds are not examined at all. So a line moving from
`leapmotor-api[image]==0.3.1` to `leapmotor-api==0.3.1` + `Pillow>=10.0.0,<13` — which is what
tripped the old check — is a no-op to the app: Pillow was already there, as that extra.

HOW THIS ASKS IT.
  1. the shell's latest release gives us its seed (payload-seed.txt) and its own tag;
  2. we fetch `mate_desktop/updater.py` FROM MateDesktop AT THAT TAG — so the verdict comes from
     the very code the shipped shell runs, not from a copy that can drift;
  3. we build a virtualenv from the SEED commit's requirements, which is what the shell build
     installed, so pip resolves the same transitive libraries the shell ended up carrying
     (anyio arrives with fastapi, Pillow with leapmotor-api[image] — neither is named in the file,
     which is exactly why reading the file alone gets it wrong);
  4. inside that interpreter we call the guard on HEAD's payload and print what it says.

HOW CLOSE THE REPRODUCTION IS — measured, not assumed. On 16/09/2026 the published v1.0.0 arm64
shell was mounted and questioned from inside (`--mate-child` runs a script with the app's own frozen
interpreter), and its versions were compared with what this venv resolves from the same seed:
fastapi, uvicorn, jinja2, python-multipart, leapmotor-api, tzdata, starlette, paho-mqtt and the two
that float — cryptography 48.0.1 and Pillow 12.3.0 — agreed **10 out of 10**. The same check also
confirmed the verdict end to end: the guard returned [], the v3.16.0 payload imported in that shell,
and GET /charges answered 200. Re-measure this if the seed ever moves; pip resolves on the day it
runs, so agreement is a fact about this seed, not a guarantee.

⚠️ INHERITED BLIND SPOT, worth knowing: the app's guard reads only the FIRST operator on a line, so
upper bounds (`<4.15`, `<13`) are invisible to it — and therefore to this check, which exists to
predict it. A shell carrying a library too NEW for the payload is accepted by both and would fail at
runtime instead. Fixing that belongs in the app's guard, not here.

⚠️ AND WHEN IT CANNOT ANSWER (no shell published, seed not in this repo, network or pip failure)
it says so as a WARNING and exits 0. A check that cannot answer must not claim the answer is "fine",
and must not block a release either — that was the old job's other failure mode, inverted.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

SHELL_REPO = "ProtossBlaster/MateDesktop"
SHELL_LATEST = f"https://api.github.com/repos/{SHELL_REPO}/releases/latest"
GUARD_AT_TAG = f"https://raw.githubusercontent.com/{SHELL_REPO}/{{tag}}/mate_desktop/updater.py"
PARTS = ("web", "poller")
TIMEOUT = 30


def _get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "mate-shell-compat"})
    token = os.environ.get("GITHUB_TOKEN", "")
    if token and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def cannot_answer(why: str) -> int:
    """Say so out loud, and let the release through."""
    print(f"::warning::desktop shell check could not answer ({why}) — this is NOT a pass")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--head", default=".", help="payload root holding web/ and poller/")
    ap.add_argument("--seed", default=None, help="override the shell's seed ref (testing)")
    ap.add_argument("--guard", default=None, help="use this updater.py instead of fetching (testing)")
    args = ap.parse_args()
    head = Path(args.head).resolve()

    seed, shell_tag = args.seed, None
    if seed is None:
        try:
            rel = json.loads(_get(SHELL_LATEST))
        except Exception as exc:                                       # noqa: BLE001
            return cannot_answer(f"the shell's releases are unreadable: {exc}")
        shell_tag = rel.get("tag_name")
        asset = next((a for a in rel.get("assets", []) if a.get("name") == "payload-seed.txt"), None)
        if not asset:
            print("No published shell carrying a seed marker — there is nothing out there to strand.")
            return 0
        try:
            seed = _get(asset["browser_download_url"], accept="*/*").decode().strip()
        except Exception as exc:                                       # noqa: BLE001
            return cannot_answer(f"the seed marker is unreadable: {exc}")

    if subprocess.run(["git", "rev-parse", "-q", "--verify", f"{seed}^{{commit}}"],
                      cwd=head, capture_output=True).returncode != 0:
        print(f"::notice::the shell reports seed '{seed}', which this repo cannot resolve — skipping")
        return 0
    print(f"published shell: {shell_tag or '(given)'} · built from Mate {seed}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 1. the guard the shipped shell actually runs
        guard = tmp / "shell_updater.py"
        if args.guard:
            guard.write_bytes(Path(args.guard).read_bytes())
        else:
            try:
                guard.write_bytes(_get(GUARD_AT_TAG.format(tag=shell_tag), accept="*/*"))
            except Exception as exc:                                   # noqa: BLE001
                return cannot_answer(f"the shell's own guard could not be fetched: {exc}")

        # 2. what that shell carries = what its seed's requirements install
        seed_reqs = []
        for part in PARTS:
            blob = subprocess.run(["git", "show", f"{seed}:{part}/requirements.txt"],
                                  cwd=head, capture_output=True)
            if blob.returncode == 0:
                p = tmp / f"{part}-seed.txt"
                p.write_bytes(blob.stdout)
                seed_reqs += ["-r", str(p)]
        if not seed_reqs:
            return cannot_answer(f"{seed} carries no requirements file to reproduce")

        venv = tmp / "shell-env"
        if subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True).returncode:
            return cannot_answer("the reproduction virtualenv could not be created")
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        pip = subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                              *seed_reqs], capture_output=True, text=True)
        if pip.returncode:
            return cannot_answer(f"the shell's own dependency set does not install: "
                                 f"{pip.stderr.strip().splitlines()[-1] if pip.stderr.strip() else '?'}")

        # 3. ask the guard, inside that interpreter
        runner = tmp / "ask.py"
        runner.write_text(
            "import importlib.util, json, pathlib, sys\n"
            "spec = importlib.util.spec_from_file_location('shell_updater', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "print(json.dumps(m.unsatisfied_requirements(pathlib.Path(sys.argv[2]))))\n")
        ask = subprocess.run([str(py), str(runner), str(guard), str(head)],
                             capture_output=True, text=True)
        if ask.returncode:
            return cannot_answer(f"the guard did not run: {ask.stderr.strip().splitlines()[-1:] or '?'}")
        unmet = json.loads(ask.stdout.strip() or "[]")

    if not unmet:
        print(f"OK — the shell built from {seed} can satisfy every requirement this payload asks for.")
        print("     (asked with the shell's own guard, not by diffing the file)")
        return 0

    print("The published shell cannot satisfy:")
    for item in unmet:
        print(f"  · {item}")
    msg = (f"the desktop shell built from {seed} cannot satisfy this payload ("
           + "; ".join(unmet) + "). Its updater will REFUSE the update and hold every desktop user "
           "on an older Mate. Build and release MateDesktop together with this one.")
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        print(f"::error::{msg}")
        return 1
    print(f"::warning::{msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
