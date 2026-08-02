""""Add to Home Screen" gives an app, not a bookmark (#213 @jose-knowee).

He asked for a native phone app. There can't be one — Mate is a recorder that has to poll for years
and a phone suspends background apps — but most of what he wanted is the launch: an icon, one tap,
full screen. That costs a manifest and six tags, and changes not one line of the interface, which
was already responsive.

Everything is checked as a FILE, with no import of the app: these must run on CI, which installs no
FastAPI.

The trap these exist for is **absolute paths**. Under the Home Assistant add-on, Mate is served
below an ingress prefix (`/api/hassio_ingress/<token>/`) and every URL in the app is relative so it
survives it. A leading "/" in the manifest sends the installed app to the host root, where there is
nothing — and it would only break for add-on users, i.e. most of them, on a surface nobody opens
twice.
"""
import json
import pathlib

STATIC = pathlib.Path(__file__).resolve().parent.parent / "web" / "static"
BASE_HTML = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" / "base.html"


def _manifest():
    return json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))


def test_the_manifest_is_valid_json_and_names_the_app():
    m = _manifest()
    assert m["name"] == "LeapMotor Mate"
    assert m["short_name"] == "Mate"          # what fits under the icon
    assert m["display"] == "standalone"       # the whole point: no browser chrome


def test_no_path_in_the_manifest_is_absolute():
    m = _manifest()
    paths = [m["start_url"], m["scope"]] + [i["src"] for i in m["icons"]]
    assert not [p for p in paths if p.startswith("/")], f"absolute path would break HA ingress: {paths}"


def test_start_url_climbs_out_of_static_to_the_app_root():
    # The manifest is served from /static/, so "./" would start the app inside /static/ — a 404 with
    # an icon on it. It has to be "../".
    m = _manifest()
    assert m["start_url"] == "../" and m["scope"] == "../"


def test_every_icon_the_manifest_promises_actually_exists():
    for icon in _manifest()["icons"]:
        f = STATIC / icon["src"]
        assert f.exists(), f"{icon['src']} is declared and missing"
        assert f.stat().st_size > 500, f"{icon['src']} looks empty"


def test_the_icon_sizes_are_the_ones_the_phones_ask_for():
    from struct import unpack
    for icon in _manifest()["icons"]:
        raw = (STATIC / icon["src"]).read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{icon['src']} is not a PNG"
        w, h = unpack(">II", raw[16:24])                      # IHDR
        assert f"{w}x{h}" == icon["sizes"], f"{icon['src']} is {w}x{h}, declared {icon['sizes']}"


def test_ios_gets_its_own_icon_because_it_ignores_the_manifest():
    # iOS reads apple-touch-icon and nothing else. 180x180, and square: it rounds the corners itself,
    # so a pre-rounded icon comes out with dark slivers where the two radii disagree.
    from struct import unpack
    raw = (STATIC / "mate-icon-180.png").read_bytes()
    assert unpack(">II", raw[16:24]) == (180, 180)
    html = BASE_HTML.read_text(encoding="utf-8")
    assert 'rel="apple-touch-icon" href="static/mate-icon-180.png"' in html


def test_the_head_links_it_all_and_stays_relative():
    html = BASE_HTML.read_text(encoding="utf-8")
    for needle in ('<link rel="manifest" href="static/manifest.webmanifest">',
                   '<meta name="theme-color" content="#0f172a">',
                   '<meta name="apple-mobile-web-app-capable" content="yes">',
                   '<meta name="mobile-web-app-capable" content="yes">',
                   '<meta name="apple-mobile-web-app-title" content="Mate">'):
        assert needle in html, f"missing from <head>: {needle}"
    assert 'href="/static/manifest' not in html, "absolute path would break HA ingress"


def test_the_theme_colour_matches_the_app_it_frames():
    # The phone paints the status bar and the splash with these; anything else and the app opens
    # with a coloured band above a dark page.
    m = _manifest()
    html = BASE_HTML.read_text(encoding="utf-8")
    assert m["theme_color"] == m["background_color"] == "#0f172a"
    assert f'content="{m["theme_color"]}"' in html
