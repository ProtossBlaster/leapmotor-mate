"""Where a frame puts the car — one rule for the poller and for the web's write after a command.

The cloud sends every coordinate twice: signals 2/3 are SIGNED, 3724/3725 (and 2190/2191) are
unsigned magnitudes, and the signed slot has arrived without its minus (#30, #43, #158, #232). Each
car's hemisphere is remembered per axis, and a reading that contradicts it is argued, not believed.
The poller learns (`learn=True`); the web applies what the poller persisted and never learns, so
the two cannot disagree about where the car is.

⚠️ poller/gps_position.py and web/gps_position.py are byte-identical copies: the two processes are
separate sys.path roots and cannot import each other (tests/test_basics.py holds them equal).
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# The physics: a car cannot teleport across the line. Driving to the other side means passing
# through zero, so a real crossing is always observed NEAR the line; a dropped sign is observed at
# full magnitude (8.6° W becoming 8.6° E is 1720 km between two polls).
NEAR_LINE_DEG = 1.0          # within ~85 km of the line a crossing is ordinary — believe it
FLIP_CONFIRMATIONS = 10      # far from it, the flip must be argued: 10 polls in a row

# Signed slot first, then the unsigned magnitudes in the order they are trusted.
SIGNALS = {"lat": ("3", ("3725", "2190")), "lon": ("2", ("3724", "2191"))}
LIMITS = {"lat": 90.0, "lon": 180.0}


@dataclass
class Memory:
    """What one car's frames taught: the sign per axis (-1.0 / 1.0; absent = never learned) and,
    per axis, how many signed polls in a row have asked for the other side. The counter is kept
    apart from the signs: poller/main.py persists the signs, and a counter among them would
    rewrite the setting on every poll."""
    signs: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)


def coordinate(raw, limit: float) -> Optional[float]:
    """A finite number within ±limit; None when absent, unreadable, NaN, infinite or out of range."""
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and abs(value) <= limit else None


def _reading(sig: dict, axis: str) -> Optional[tuple[Optional[float], float]]:
    """(signed, magnitude) of one axis: the signed value when it is a nonzero coordinate, else the
    first nonzero unsigned one, else a zero the frame did send. None when the frame carries no
    coordinate for this axis at all — absent and unreadable are no reading, a sent 0 is one."""
    signed_id, unsigned_ids = SIGNALS[axis]
    signed = coordinate(sig.get(signed_id), LIMITS[axis])
    if signed:
        return signed, abs(signed)
    unsigned = [u for u in (coordinate(sig.get(i), LIMITS[axis]) for i in unsigned_ids) if u is not None]
    if any(unsigned):
        return None, abs(next(u for u in unsigned if u))
    if signed is not None or unsigned:
        return None, 0.0
    return None


def resolve(mem: Memory, sig: dict, *, learn: bool) -> tuple[float, float]:
    """(latitude, longitude) of a frame. A frame that lacks one of the two axes places the car
    nowhere — (0, 0), the no-fix marker the Overview and the trip already skip — and teaches the
    memory nothing, so a partial frame can neither move the car to the equator or the meridian nor
    take part in a sign argument."""
    readings = {axis: _reading(sig, axis) for axis in ("lat", "lon")}
    if None in readings.values():
        return 0.0, 0.0
    return (_resolve_axis(mem, "lat", *readings["lat"], learn=learn),
            _resolve_axis(mem, "lon", *readings["lon"], learn=learn))


def _resolve_axis(mem: Memory, axis: str, s: Optional[float], magnitude: float, *, learn: bool) -> float:
    """Resolve one GPS axis. The signed signal (2/3) is authoritative and refreshes the
    remembered sign; when only the unsigned signal is present, re-apply the remembered sign
    to its magnitude (#43). With no memory yet (a fresh install before any signed poll) the
    unsigned value is used as-is, so east-of-Greenwich cars are never affected.

    A signed read that contradicts a hemisphere we already know is only believed near the line, or
    after FLIP_CONFIRMATIONS polls in a row say the same thing (#232). Without `learn` nothing is
    counted or learned: the web writes one row on a button and cannot count polls, so it refuses
    every far-from-the-line flip and follows the sign the poller persists."""
    if s is not None:
        proposed = -1.0 if s < 0 else 1.0
        known = mem.signs.get(axis)
        if known is None or known == proposed or s < 0.0 or abs(s) <= NEAR_LINE_DEG:
            # Nothing to protect (fresh install), nothing to argue about, a value that proves its
            # own sign, or a car genuinely at the line. Any of the four also settles a pending flip.
            #
            # 🔑 `s < 0` is why the guard is one-directional: a LOST sign can only ever surface as
            # a positive number, because the signals it would be confused with are magnitudes and
            # have no minus to drop. A negative reading is therefore evidence, not noise — doubting
            # it would strand a car that moved west or south for ten polls and buy nothing.
            if learn:
                mem.signs[axis] = proposed
                mem.pending.pop(axis, None)
            return s
        if not learn:
            return abs(s) * known
        seen = mem.pending.get(axis, 0) + 1
        mem.pending[axis] = seen
        if seen >= FLIP_CONFIRMATIONS:
            # Argued for long enough to be real: a car shipped across the line with its SIM dark
            # never gets to be seen near it, and refusing forever would strand that owner.
            log.warning("GPS %s: the opposite hemisphere has now been reported %d polls running — "
                        "accepting it and re-learning the sign", axis, seen)
            mem.signs[axis] = proposed
            mem.pending.pop(axis, None)
            return s
        # NO coordinate in this line: the poller log ships inside the diagnostics bundle, which
        # strips the GPS signals precisely so it can be attached in public (#232 asked for it).
        log.warning("GPS %s: cloud reported the opposite hemisphere far from the line (%d/%d) — "
                    "a car cannot cross it in one poll, keeping the remembered sign",
                    axis, seen, FLIP_CONFIRMATIONS)
        return abs(s) * known
    return magnitude * mem.signs.get(axis, 1.0)
