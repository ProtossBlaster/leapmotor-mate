"""Load the same pinned API runtime from either Mate process (also frozen Desktop)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for directory in (ROOT / 'poller' / 'vendor', ROOT / 'poller' / 'mate_api_runtime'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
os.environ['MATE_API_V2'] = '1'
from runtime_paths import configure
configure()
