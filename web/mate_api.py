"""Load the pinned API runtime from either Mate process (also frozen Desktop).

Puts the vendored client and its runtime on the path and configures the installation's paths.
There is one cloud client, Mate's own; nothing is chosen at startup any more.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for directory in (ROOT / 'poller' / 'vendor', ROOT / 'poller' / 'mate_api_runtime'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
from runtime_paths import configure
configure()
