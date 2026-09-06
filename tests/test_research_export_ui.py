import pathlib
import shutil
import subprocess

import pytest


def test_download_ui_waits_prevents_duplicate_clicks_and_recovers():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for download UI behavior test")
    root = pathlib.Path(__file__).resolve().parents[1]
    subprocess.run([node, str(root / "tests/research_export_ui.cjs"),
                    str(root / "web/static/research-export.js")], check=True)
