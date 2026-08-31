"""An absent value published as "" makes Home Assistant complain, not shrug (30/08 audit).

`pub()` writes an empty string when a value is None, and Home Assistant reads that as a state — so
a numeric entity (`device_class: power`, `unit: W`) receiving "" logs a conversion error on every
poll where the car does not report the signal. Two sensors already carry the antidote in their
discovery payload, `{{ value if value else none }}`, which turns the empty string into a genuine
unknown. `climate_power` and `fan_level` did not, and they are precisely the two signals a car
stops reporting the moment the climate is off.

Not fixed at `pub()`: an empty retained payload is also how a topic is cleared, and the two sensors
that already had the template show the intended shape. The fix belongs in the discovery config.

→ signal-absent-is-not-signal-zero: absent is not zero, and it is not "" either.

CI-safe: reads the bridge source, no broker.
"""
import pathlib
import re

import pytest

SRC = pathlib.Path("poller/mqtt.py").read_text()

# Entities that carry a NUMBER and whose signal the car legitimately stops sending.
NUMERIC_OPTIONAL = ("climate_power", "fan_level", "data_age", "frame_ts")


def _discovery_of(key):
    """The discovery declaration for `key`, not the `pub()` call that shares its name.

    Anchored on the declaration's own shape — a sensor tuple `("key", "Name", {...})` or a
    `cfg("<platform>", "key", {...})` — because searching for the bare key finds the publisher
    first and would pass or fail on the wrong line."""
    tup = re.search(rf'\(\s*"{key}",\s*"[^"]+",\s*\{{(.*?)\}}\s*\)', SRC, re.S)
    if tup:
        return tup.group(1)
    cfg = re.search(rf'cfg\(\s*"[a-z]+",\s*"{key}",\s*\{{(.*?)\n\s*\}}\)', SRC, re.S)
    return cfg.group(1) if cfg else None


@pytest.mark.parametrize("key", NUMERIC_OPTIONAL)
def test_its_discovery_turns_an_empty_payload_into_unknown(key):
    """Each of these is published as "" when the car is silent about it."""
    block = _discovery_of(key)
    assert block is not None, f"{key}: no discovery declaration found"
    assert "_EMPTY_NONE" in block, \
        f"{key}: publishes '' with no empty-to-none template — HA logs a conversion error"


def test_the_template_itself_still_says_what_it_says():
    assert '_EMPTY_NONE = "{{ value if value else none }}"' in SRC
