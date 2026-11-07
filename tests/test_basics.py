"""Lightweight regression guards: pure math, duplicated-file drift, version/changelog sync."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_haversine_known_distance():
    import db  # poller/db
    # Rome (Colosseum) -> Milan (Duomo) is ~478 km
    d = db.haversine_km(41.8902, 12.4922, 45.4642, 9.1900)
    assert 470 < d < 485


def test_zero_distance():
    import db
    assert db.haversine_km(45.0, 9.0, 45.0, 9.0) == 0


def test_crypto_copies_identical():
    a = (ROOT / "poller" / "crypto.py").read_bytes()
    b = (ROOT / "web" / "crypto.py").read_bytes()
    assert a == b, "poller/crypto.py and web/crypto.py have drifted — keep them byte-identical"


def test_gps_position_copies_identical():
    a = (ROOT / "poller" / "gps_position.py").read_bytes()
    b = (ROOT / "web" / "gps_position.py").read_bytes()
    assert a == b, "poller/gps_position.py and web/gps_position.py have drifted — the poller and the web" \
                   " would disagree about where the car is"


def test_session_share_copies_identical():
    a = (ROOT / "poller" / "session_share.py").read_bytes()
    b = (ROOT / "web" / "session_share.py").read_bytes()
    assert a == b, "poller/session_share.py and web/session_share.py have drifted"


def test_version_matches_changelog():
    main = (ROOT / "web" / "main.py").read_text()
    ver = re.search(r'MATE_VERSION\s*=\s*"([^"]+)"', main).group(1)
    # A pre-release / dev build (e.g. "1.11.18-dev") runs on the test Docker before the release and
    # is intentionally NOT in the changelog yet — only enforce version↔changelog sync for releases.
    if "-" in ver:
        return
    top = re.search(r'^## \[([0-9][^\]]*)\]', (ROOT / "CHANGELOG.md").read_text(), re.M).group(1)
    assert ver == top, f"MATE_VERSION {ver} != latest CHANGELOG entry [{top}]"
