"""Load the same pinned API runtime from either Mate process (also frozen Desktop)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for directory in (ROOT / 'poller' / 'vendor', ROOT / 'poller' / 'mate_api_runtime'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
from runtime_paths import configure
configure()

from migration_activation import activate_installation
activate_installation()
