"""Where a frame puts the car — one rule for the poller and for the web's write after a command.

The cloud sends every coordinate twice: signals 2/3 are SIGNED, 3724/3725 (and 2190/2191) are
unsigned magnitudes, and the signed slot has arrived without its minus (#30, #43, #158, #232). Each
car's hemisphere is remembered per axis, and a reading that contradicts it is argued, not believed.
Until the odometer moves on, the place a frame put the car at that reading puts back a minus a
later frame dropped (Anchor), which is what a parked car needs. The poller learns (`learn=True`);
the web applies what the poller persisted and never learns, so the two cannot disagree about where
the car is.

⚠️ poller/gps_position.py and web/gps_position.py are byte-identical copies: the two processes are
separate sys.path roots and cannot import each other (tests/test_basics.py holds them equal).
"""
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# The physics: a car cannot teleport across the line. Driving to the other side means passing
# through zero, so a real crossing is always observed NEAR the line; a dropped sign is observed at
# full magnitude (8.6° W becoming 8.6° E is 1720 km between two polls).
NEAR_LINE_DEG = 1.0          # within ~85 km of the line a crossing is ordinary — believe it
FLIP_CONFIRMATIONS = 10      # far from it, the flip must be argued: 10 frames in a row
# An odometer reading that has not changed allows less than a kilometre of driving since the anchor;
# the rest is the error of a fix taken in a car park. A car carried on a lorry or a ferry ends up
# further away, and a larger radius would hold a real crossing near the line for longer.
PARKED_RADIUS_KM = 2.0
# A frame clock further ahead of ours is no clock. A stamp ahead holds later frames back until ours
# catches up; a crossing among them is learned only within 1° of the line, so keep it to minutes.
FUTURE_CLOCK_MS = 300_000

# Signed slot first, then the unsigned magnitudes in the order they are trusted.
SIGNALS = {"lat": ("3", ("3725", "2190")), "lon": ("2", ("3724", "2191"))}
LIMITS = {"lat": 90.0, "lon": 180.0}


@dataclass
class Anchor:
    """Where the car was at an odometer reading: the first new frame at that reading whose signed
    coordinates were believed as sent, with no argument open. It puts back a minus a later frame
    drops, never removes one."""
    lat: float
    lon: float
    km: int


@dataclass
class Memory:
    """What one car's frames taught: the sign per axis (-1.0 / 1.0; absent = never learned), per
    axis how many new frames in a row have asked for the other side, the clock of the newest frame
    seen, the anchor, the odometer reading at the last change of sign and the last one seen, and
    how many new frames in a row the anchor has overruled. The counters are kept apart from the signs:
    poller/main.py persists the signs, and a counter among them would rewrite the setting on every
    poll."""
    signs: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    newest_frame_ts: int = 0
    anchor: Optional[Anchor] = None
    learned_at_km: Optional[int] = None
    last_km: Optional[int] = None
    overruled: int = 0


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


def odometer(raw) -> Optional[int]:
    """The whole kilometre an odometer reading falls in; None when there is no usable reading."""
    value = coordinate(raw, math.inf)
    return int(value) if value and value > 0 else None


def is_new_observation(mem: Memory, frame_ts: int, now_ms: int) -> bool:
    """Whether a frame tells the memory something it has not heard: one newer than every frame
    before. A repeat, or an older frame replayed, is the same observation again and can neither
    count towards a flip nor teach a sign. A frame without a usable clock — none, not positive, or
    more than FUTURE_CLOCK_MS ahead of ours — cannot be ordered, so it counts as the poll it arrived
    in: refusing it instead would hold a car that really changed sides on the wrong one for good."""
    if frame_ts <= 0 or frame_ts > now_ms + FUTURE_CLOCK_MS:
        return True
    if frame_ts <= mem.newest_frame_ts:
        return False
    mem.newest_frame_ts = frame_ts
    return True


def resolve(mem: Memory, sig: dict, *, learn: bool, frame_ts: int = 0, now_ms: int = 0) -> tuple[float, float]:
    """(latitude, longitude) of a frame. A frame that lacks one of the two axes places the car
    nowhere — (0, 0), the no-fix marker the Overview and the trip already skip — and takes no part
    in a sign argument, so a partial frame can neither move the car to the equator or the meridian
    nor flip it. With `learn`, only a new observation (is_new_observation) counts or learns, and a
    frame without a fix is heard too, its odometer reading with it; any other frame is placed by
    what the memory holds."""
    readings = {axis: _reading(sig, axis) for axis in ("lat", "lon")}
    km = odometer(sig.get("1318")) or mem.last_km     # a frame without one is where the car was
    learn = learn and is_new_observation(mem, frame_ts, now_ms)
    if learn:
        mem.last_km = km                              # a frame without a fix still says the car drove
    if None in readings.values():
        return 0.0, 0.0
    held = _held_by_anchor(mem, km, readings)
    if held:
        if learn:
            mem.pending.clear()                       # placed on the remembered sides: a run is broken
            _note_overruled(mem, readings)
        return held
    if learn:
        mem.overruled = 0
    before, disputed = dict(mem.signs), bool(mem.pending)
    lat = _resolve_axis(mem, "lat", *readings["lat"], learn=learn)
    lon = _resolve_axis(mem, "lon", *readings["lon"], learn=learn)
    if not learn:
        return lat, lon
    if any(before.get(axis) not in (None, mem.signs[axis]) for axis in mem.signs):
        # No anchor at this reading: the frame that just changed the sign must not be confirmed by
        # the next one at the same place.
        mem.learned_at_km = km
    elif (km and km != mem.learned_at_km and not disputed           # a frame ending an argument anchors nothing
          and (mem.anchor is None or mem.anchor.km != km)
          and (lat, lon) == (readings["lat"][0], readings["lon"][0])):
        mem.anchor = Anchor(lat, lon, km)
    return lat, lon


def _held_by_anchor(mem: Memory, km: Optional[int], readings: dict) -> Optional[tuple[float, float]]:
    """The frame's position on the anchor's sides, when the odometer still shows the anchor's reading
    and that puts it next to the anchor; None otherwise. A car that has driven less than a kilometre
    from where a frame placed it is still on that side of the line, so a positive reading there is a
    dropped minus, however near the line it stands. A negative reading is not doubted: it goes to
    the rule, which believes it, as a lost sign cannot add a minus (_resolve_axis). An anchor on
    other sides than the remembered signs — from before a change of sign, or one the history
    overruled at startup — places nothing."""
    anchor = mem.anchor
    if (anchor is None or km != anchor.km
            or mem.signs != {"lat": math.copysign(1.0, anchor.lat), "lon": math.copysign(1.0, anchor.lon)}
            or any(s is not None and s < 0 < side
                   for (s, _), side in zip(readings.values(), (anchor.lat, anchor.lon)))):
        return None
    lat = math.copysign(readings["lat"][1], anchor.lat)
    lon = math.copysign(readings["lon"][1], anchor.lon)
    return (lat, lon) if _km_between(lat, lon, anchor.lat, anchor.lon) <= PARKED_RADIUS_KM else None


def _note_overruled(mem: Memory, readings: dict) -> None:
    """A frame that contradicts the anchor's sides is logged on the first frame of a run and every
    hundredth after: the car may stay awake for hours."""
    anchor = {"lat": mem.anchor.lat, "lon": mem.anchor.lon}
    if all(s is None or (s < 0) == (anchor[axis] < 0) for axis, (s, _) in readings.items()):
        mem.overruled = 0
        return
    mem.overruled += 1
    if mem.overruled == 1 or mem.overruled % 100 == 0:
        # NO coordinate in this line, for the same reason as the refusal below.
        log.warning("GPS: the cloud reported the opposite sign for a car that has not driven a "
                    "kilometre since it was placed — keeping its sign (frames in a row: %d)",
                    mem.overruled)


def _km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


def record(mem: Memory) -> str:
    """What outlives a restart and what the web reads: the anchor, the odometer readings at the
    last change of sign and the last frame, and the axes an argument is open on."""
    anchor = mem.anchor
    return json.dumps({"anchor": [anchor.lat, anchor.lon, anchor.km] if anchor else None,
                       "learned_at_km": mem.learned_at_km, "last_km": mem.last_km,
                       "disputed": sorted(mem.pending)})


def restore(mem: Memory, text: Optional[str]) -> None:
    """Load a `record`. An open argument stays open; its count starts over, as on every restart."""
    try:
        data = json.loads(text or "{}")
        lat, lon, km = data.get("anchor") or (None, None, None)
        learned_at_km, last_km = odometer(data.get("learned_at_km")), odometer(data.get("last_km"))
        pending = {axis: 0 for axis in ("lat", "lon") if axis in (data.get("disputed") or ())}
    except (TypeError, ValueError, AttributeError):
        return
    mem.learned_at_km, mem.last_km, mem.pending = learned_at_km, last_km, pending
    lat, lon, km = coordinate(lat, LIMITS["lat"]), coordinate(lon, LIMITS["lon"]), odometer(km)
    if lat and lon and km:
        mem.anchor = Anchor(lat, lon, km)


def _resolve_axis(mem: Memory, axis: str, s: Optional[float], magnitude: float, *, learn: bool) -> float:
    """Resolve one GPS axis. The signed signal (2/3) is authoritative and refreshes the
    remembered sign; when only the unsigned signal is present, re-apply the remembered sign
    to its magnitude (#43). With no memory yet (a fresh install before any signed poll) the
    unsigned value is used as-is, so east-of-Greenwich cars are never affected.

    A signed read that contradicts a hemisphere we already know is only believed near the line, or
    after FLIP_CONFIRMATIONS new frames in a row say the same thing (#232). Without `learn` nothing
    is counted or learned: the web writes one row on a button and cannot count frames, so it refuses
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
            log.warning("GPS %s: the opposite hemisphere has now been reported %d frames running — "
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
