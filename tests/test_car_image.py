"""Live Overview car image (car_image.py).

Mate composes the per-vehicle layer package to reflect the live state (charge cable / charging
animation / trunk) instead of a static render. These cover the Mate-specific bridge
(`get_latest_status()` dict → the duck-typed VehicleStatus the compositor reads), the static
fallback, and the PNG-vs-animated-WebP selection."""
import io
import zipfile

import pytest

import car_image


# ── the status bridge (pure, no Pillow needed) ──────────────────────────────────
def test_bridge_plug_and_charging():
    st = car_image._status_obj({"plug_connected": 1, "charging": 0})
    assert st.is_plugged is True and st.is_charging is False

    sc = car_image._status_obj({"charging": 1})
    assert sc.is_charging is True
    assert sc.is_plugged is True            # charging implies the cable is in
    assert sc.battery.is_charging is True


def test_bridge_trunk_maps_to_tailgate():
    assert car_image._status_obj({"trunk_open": 1}).doors.bbcm_back_door_status == 1
    assert car_image._status_obj({}).doors.bbcm_back_door_status == 0


def test_bridge_maps_four_doors():
    # Mate's per-door keys → the library's door fields (driver = left front, passenger = right front).
    d = car_image._status_obj({
        "door_driver_open": 1, "door_passenger_open": 0,
        "door_rear_left_open": 1, "door_rear_right_open": 0,
    }).doors
    assert d.lbcm_driver_door_status == 1
    assert d.rbcm_driver_door_status == 0
    assert d.lbcm_left_rear_door_status == 1
    assert d.rbcm_right_rear_door_status == 0


def test_bridge_maps_left_windows():
    # Only the 2 left windows are drawn; open → non-zero percent (glass removed), closed → 0 (glass).
    w = car_image._status_obj({"window_fl_open": 1, "window_rl_open": 0}).windows
    assert w.left_front_window_percent != 0
    assert w.left_rear_window_percent == 0


def test_open_left_door_suppresses_window_glass():
    # The open front-left (driver) door overlaps BOTH left windows → suppress both glasses.
    wf = car_image._status_obj({"door_driver_open": 1}).windows
    assert wf.left_front_window_percent != 0
    assert wf.left_rear_window_percent != 0
    # The rear-left door suppresses only its own glass (front unaffected).
    wr = car_image._status_obj({"door_rear_left_open": 1}).windows
    assert wr.left_front_window_percent == 0
    assert wr.left_rear_window_percent != 0


def test_bridge_all_closed_when_empty():
    st = car_image._status_obj({})
    d = st.doors
    assert (d.lbcm_driver_door_status, d.rbcm_driver_door_status, d.lbcm_left_rear_door_status,
            d.rbcm_right_rear_door_status, d.bbcm_back_door_status) == (0, 0, 0, 0, 0)
    assert (st.windows.left_front_window_percent, st.windows.left_rear_window_percent) == (0, 0)


def test_bridge_handles_none_and_empty():
    for s in (None, {}):
        st = car_image._status_obj(s)
        assert st.is_plugged is False and st.is_charging is False
        assert st.doors.bbcm_back_door_status == 0


def test_static_image_on_bad_bytes_returns_none():
    assert car_image.static_image(b"not a zip") is None


# ── compose end-to-end (needs Pillow — present via leapmotor-api[image]) ─────────
def _tiny_package() -> bytes:
    """A minimal layer package (transparent stand-ins) so compose() can run without the real car."""
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    names = [
        "carpic_body.png", "carpic_hood_close.png",
        "carpic_rightbehind_close.png", "carpic_rightfront_close.png",
        "carpic_leftbehind_close.png", "carpic_leftfront_close.png",
        "carpic_leftfront_window_close.png", "carpic_leftbehind_window_close.png",
        "carpic_charge_open.png", "carpic_charge1.png", "carpic_for_tripsum.png",
    ] + [f"carpic_charge{i}.png" for i in range(2, 16)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            b = io.BytesIO()
            Image.new("RGBA", (12, 8), (0, 0, 0, 0)).save(b, format="PNG")
            z.writestr(f"android/xxhdpi/{n}", b.getvalue())
    return buf.getvalue()


def test_compose_static_png_when_idle():
    pkg = _tiny_package()
    car_image.clear_cache()
    body, mime = car_image.compose(pkg, {})
    assert mime == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n"


def test_compose_animated_webp_when_charging():
    pkg = _tiny_package()
    car_image.clear_cache()
    body, mime = car_image.compose(pkg, {"charging": 1})
    assert mime == "image/webp" and body[:4] == b"RIFF"


def test_static_image_extracts_tripsum_from_real_shape():
    pkg = _tiny_package()
    assert car_image.static_image(pkg) is not None


# ── the animation: which frames, and which way round (#211) ─────────────────────
def _animated_package(dot_x: list[int], *, body_right_edge: int = 60) -> bytes:
    """A package whose charging frames carry one bright dot at the given x positions.

    The car is a solid block from the left edge to `body_right_edge`; the dot stands in for the
    highlight travelling along the cable. Feed the positions in the order the frames are numbered
    and the measurement has to work out which end is the car."""
    pytest.importorskip("PIL")
    from PIL import Image
    W, H = 120, 40
    body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for x in range(body_right_edge):
        for y in range(H):
            body.putpixel((x, y), (200, 200, 200, 255))

    def _png(img):
        b = io.BytesIO()
        img.save(b, format="PNG")
        return b.getvalue()

    files = {"carpic_body.png": _png(body)}
    for n in ("carpic_hood_close.png", "carpic_rightbehind_close.png", "carpic_rightfront_close.png",
              "carpic_leftbehind_close.png", "carpic_leftfront_close.png",
              "carpic_leftfront_window_close.png", "carpic_leftbehind_window_close.png",
              "carpic_charge_open.png", "carpic_charge1.png", "carpic_for_tripsum.png"):
        files[n] = _png(Image.new("RGBA", (W, H), (0, 0, 0, 0)))
    for i, x in enumerate(dot_x, start=2):
        f = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                f.putpixel((x + dx, H // 2 + dy), (255, 255, 255, 255))
        files[f"carpic_charge{i}.png"] = _png(f)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(f"android/xxhdpi/{n}", b)
    return buf.getvalue()


def _order_of(pkg_bytes):
    car_image.clear_cache()
    return car_image._order(pkg_bytes)


def test_flow_towards_the_car_keeps_the_file_order():
    # Dot starts far from the car and ends against it — numbered the right way round already.
    assert _order_of(_animated_package([110, 100, 90, 80, 70, 63])) == [2, 3, 4, 5, 6, 7]


def test_flow_away_from_the_car_is_played_backwards():
    # The T03 case @banolka reported: frame 2 is already at the car, so the cycle reads as energy
    # leaving it. Same pixels, played the other way.
    assert _order_of(_animated_package([63, 70, 80, 90, 100, 110])) == [7, 6, 5, 4, 3, 2]


def test_ends_too_close_to_call_are_left_alone():
    # Both ends the same distance from the car → no evidence either way → don't touch the order.
    assert _order_of(_animated_package([100, 90, 80, 90, 100])) == [2, 3, 4, 5, 6]


def test_every_frame_in_the_package_is_played():
    # The library stopped at carpic_charge15; the real B10 package ships 18, and the three it
    # dropped are the ones where the pulse reaches the car.
    pkg = _animated_package([110 - 2 * i for i in range(17)])          # carpic_charge2..18
    assert len(_order_of(pkg)) == 17
    car_image.clear_cache()
    body, mime = car_image.compose(pkg, {"charging": 1})
    assert mime == "image/webp"
    from PIL import Image
    assert Image.open(io.BytesIO(body)).n_frames == 17


def test_a_single_frame_package_still_composes():
    pkg = _animated_package([80])
    car_image.clear_cache()
    body, mime = car_image.compose(pkg, {"charging": 1})
    assert mime == "image/webp" and body[:4] == b"RIFF"


def test_measurement_is_cached_per_package():
    pkg = _animated_package([63, 70, 80, 90, 100, 110])
    car_image.clear_cache()
    first = car_image._order(pkg)
    assert car_image._parsed["order"] is first          # second call must not re-measure
    assert car_image._order(pkg) is first
    car_image.clear_cache()
    assert car_image._parsed["order"] is None
