"""Live Overview car image.

Composes the per-vehicle car-picture *layer package* to reflect the current state (charge cable,
charging animation, trunk) instead of serving a single static render — mirrors the official app.
The model + colour are baked into the downloaded package, so this works for any car. Falls back to
the package's static image on any problem, so the Overview never breaks.

Reflects the charge cable (+ charging animation), the tailgate, all 4 doors, and the 2 left-side
windows (the only ones drawn in the 3/4 view) — driven by the body state in `positions`. The right
windows and the front hood have no layer/signal, so they're never shown (same as the official app).

The charging animation is built here rather than by the library's `compose_animated`, for two
reasons found on the real packages (#211):

* **Which frames.** The library plays a hardcoded `carpic_charge2..15`. The B10's package ships
  **18** of them, so the last three — the ones where the pulse reaches the car — were never drawn:
  the pulse died halfway down the cable and restarted at the wallbox. We play every frame the
  package actually contains.
* **Which direction.** @banolka reports the T03 drawing the flow *out of* the car, in the official
  app as well as here — the frames come from the cloud, both apps just play them in file order. The
  order is ours to choose, so we **measure** it instead of keeping a per-model table by hand: a
  hardcoded "reverse it for the T03" would silently break the day Leapmotor fixes the artwork.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

# Decoding the ~39-layer package is the costly part, so keep the parsed package in memory and
# re-decode only when the bytes change (i.e. the car/colour package was re-downloaded). The
# playback order is measured from those same layers, so it's cached alongside them.
_parsed: dict = {"key": None, "pkg": None, "order": None}


def _package(package_bytes: bytes):
    from leapmotor_api.image import CarImagePackage
    key = len(package_bytes)
    if _parsed["key"] != key or _parsed["pkg"] is None:
        _parsed["pkg"] = CarImagePackage.from_zip(package_bytes)
        _parsed["key"] = key
        _parsed["order"] = None
    return _parsed["pkg"]


def _status_obj(status: dict):
    """A minimal duck-typed VehicleStatus — only the fields `leapmotor_api.image` reads, mapped from
    Mate's `get_latest_status()`: the **4 doors** + tailgate, the **2 left-side windows** (the only
    ones the 3/4 render draws), and the charge cable (plug/charging). Old rows (pre-migration) report
    None for the per-door/window keys → treated as closed."""
    s = status or {}
    charging = bool(s.get("charging"))
    plugged = bool(s.get("plug_connected")) or charging

    def _open(key):
        return 1 if s.get(key) else 0

    return NS(
        doors=NS(
            lbcm_driver_door_status=_open("door_driver_open"),        # driver  = left front
            rbcm_driver_door_status=_open("door_passenger_open"),     # passenger = right front
            lbcm_left_rear_door_status=_open("door_rear_left_open"),
            rbcm_right_rear_door_status=_open("door_rear_right_open"),
            bbcm_back_door_status=_open("trunk_open"),                # tailgate
        ),
        windows=NS(
            # The compositor draws the closed-window glass when percent == 0. Drop it when the open door
            # would overlap it, otherwise the glass floats over the swung-out door. The open front-left
            # (driver) door overlaps BOTH left windows, so it suppresses both glasses; the rear-left door
            # suppresses only its own (graphic fix).
            left_front_window_percent=30 if (s.get("window_fl_open") or s.get("door_driver_open")) else 0,
            left_rear_window_percent=30 if (s.get("window_rl_open") or s.get("door_rear_left_open")
                                            or s.get("door_driver_open")) else 0,
        ),
        is_plugged=plugged,
        is_charging=charging,
        battery=NS(is_charging=charging),
    )


_FRAME_MS = 200          # per frame, as the library used — the cloud ships no timing of its own
_GLOW_MIN = 40           # 0-255; the travelling highlight peaks 150+ above the still cable
_MASK_LONG_SIDE = 160    # the body silhouette is only needed coarsely, and full size is slow


def _charge_frames(pkg) -> list[int]:
    """The animation frame numbers this package actually carries, ascending.

    `carpic_charge1.png` is excluded: it's the grey idle cable drawn when the plug is in but
    nothing is flowing, not part of the cycle."""
    out = []
    for name in pkg._images:
        if name.startswith("carpic_charge") and name.endswith(".png"):
            n = name[len("carpic_charge"):-len(".png")]
            if n.isdigit() and int(n) >= 2:
                out.append(int(n))
    return sorted(out)


def _glow_positions(pkg, nums: list[int]) -> list[tuple[int, int] | None]:
    """Where the travelling highlight sits in each frame.

    The cable, the wallbox and the car are identical in every frame; only the highlight moves. So
    the darkest value each pixel ever takes IS the frame without it — subtract that and what's left
    is the highlight alone. All of it runs inside Pillow (C), not per-pixel Python."""
    from PIL import ImageChops
    frames = [pkg._images[f"carpic_charge{n}.png"].convert("RGB") for n in nums]
    still = frames[0]
    for f in frames[1:]:
        still = ImageChops.darker(still, f)
    out: list[tuple[int, int] | None] = []
    for f in frames:
        lit = ImageChops.difference(f, still).convert("L").point(lambda v: 255 if v > _GLOW_MIN else 0)
        box = lit.getbbox()
        out.append(None if box is None else ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))
    return out


def _distance_to_body(pkg, point: tuple[int, int]) -> float | None:
    """Distance from `point` to the nearest solid pixel of the car, in downscaled units.

    Endpoint x alone doesn't separate the two ends — on the B10 the cable leaves the wallbox at
    x=992 and meets the car at x=934, 58 px apart on a 1125-wide canvas. Distance to the car's
    silhouette separates them six-fold (61 vs 10), because one end is literally plugged into it."""
    body = pkg._images.get("carpic_body.png")
    if body is None:
        return None
    scale = max(1, max(body.size) // _MASK_LONG_SIDE)
    mask = body.split()[3].resize((body.width // scale, body.height // scale))
    px = mask.load()
    px_, py = point[0] / scale, point[1] / scale
    best = None
    for y in range(mask.height):
        for x in range(mask.width):
            if px[x, y] > 128:
                d = (x - px_) ** 2 + (y - py) ** 2
                if best is None or d < best:
                    best = d
    return None if best is None else best ** 0.5


def _playback_order(pkg) -> list[int]:
    """The frame numbers in the order they should be played — measured, not configured.

    The pulse must END at the car. So: find it in the first and the last frame, and see which of
    the two is nearer the car's silhouette. If it's the first, the package is numbered backwards
    and we play it in reverse. Anything unclear — one frame, no highlight found, or the two ends
    too close to call — keeps the file order, i.e. exactly the behaviour before this measurement
    existed."""
    nums = _charge_frames(pkg)
    if len(nums) < 2:
        return nums
    try:
        pos = _glow_positions(pkg, nums)
        head = next((p for p in pos if p), None)
        tail = next((p for p in reversed(pos) if p), None)
        if head is None or tail is None or head == tail:
            return nums
        d_head, d_tail = _distance_to_body(pkg, head), _distance_to_body(pkg, tail)
        if d_head is None or d_tail is None:
            return nums
        far = max(d_head, d_tail)
        if far <= 0 or abs(d_head - d_tail) < 0.25 * far:   # too close to call → leave it alone
            return nums
        return list(reversed(nums)) if d_head < d_tail else nums
    except Exception:
        return nums                                          # never let a measurement break the image


def _order(package_bytes: bytes) -> list[int]:
    pkg = _package(package_bytes)
    if _parsed["order"] is None:
        _parsed["order"] = _playback_order(pkg)
    return _parsed["order"]


def compose(package_bytes: bytes, status: dict) -> tuple[bytes, str]:
    """Return ``(image_bytes, media_type)`` reflecting the live state — an animated WebP while
    charging, a static PNG otherwise. Raises on any problem so the caller can fall back."""
    import io

    from PIL import Image
    from leapmotor_api.image import _build_layer_list

    pkg, st = _package(package_bytes), _status_obj(status)
    if not st.is_charging:
        return pkg.compose(st), "image/png"

    order = _order(package_bytes)
    layers = _build_layer_list(st)
    # Everything except the animated frame itself; `carpic_charge_open.png` is the open flap and
    # stays put, so it belongs to the base.
    base = pkg._composite_layers(
        [n for n in layers if not n.startswith("carpic_charge") or n == "carpic_charge_open.png"])
    frames = []
    for n in order:
        layer = pkg._images.get(f"carpic_charge{n}.png")
        frames.append(Image.alpha_composite(base, layer) if layer else base.copy())
    if not frames:
        return pkg.compose(st), "image/png"

    buf = io.BytesIO()
    frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:],
                   duration=_FRAME_MS, loop=0, lossless=True)
    return buf.getvalue(), "image/webp"


def static_image(package_bytes: bytes) -> bytes | None:
    """The package's pre-rendered static car PNG — the fallback / legacy behaviour."""
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(package_bytes)) as z:
            return z.read("android/xxhdpi/carpic_for_tripsum.png")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None


def clear_cache() -> None:
    _parsed["key"] = None
    _parsed["pkg"] = None
    _parsed["order"] = None
