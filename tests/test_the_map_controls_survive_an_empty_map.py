"""#270 @speshos — the map's two boxes must not disappear together with the map.

«Trips shown» and «Stations shown» are set from two number boxes that live on the map's own legend
row, and NOWHERE else: they are not in Settings, there is no other page, no URL parameter. That row
was inside `{% if track %}`, i.e. inside the branch that only renders when there IS something to
draw.

So the setting could lock the user out of the setting. Set «Trips shown» to 1, land on a most-recent
trip that happens to carry no GPS point (a short hop out of an underground garage is enough), and
the map is replaced by the empty-state card — taking the only box that could undo it with it. What
is left on screen says «No GPS data yet — drive a bit and your map will fill in», which is not what
happened and sends the reader to do the one thing that cannot help: the reporter read it exactly
that way («I assume the only way to show the map would be to drive the car»).

Two things are fixed here and they are separate:

  · the boxes always render — they are preferences, they do not describe the map, and a control the
    user cannot reach is worse than a control that is briefly pointless. The legend COUNTERS
    (routes/places/points) do describe the map and stay behind `{% if track %}`, so a fresh install
    still shows a bare empty state and not «Routes (0)»;
  · the empty state tells which of the two empties it is.

⚠️ Simplification, declared: «is a filter what hid it?» is answered by `trips_shown` alone, not by
asking the database whether any GPS point exists at all. A brand-new install carrying a leftover
non-zero box therefore reads the filtered message — and setting the box to 0, which is what that
message tells them to do, immediately turns it back into the «no data yet» one. It self-corrects.
"""
import pathlib

import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the page")

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"

# One trip, one solid run, two points — enough to put the page down the branch that draws a map.
UN_VIAGGIO = [[{"points": [[45.46, 9.19], [45.47, 9.20]], "gap": False}]]


class _Richiesta:
    """base.html reads exactly one thing off the request: the add-on ingress prefix."""
    headers: dict = {}


def _render(track=(), trips_shown=0, stations_top_n=15, places=(), stations=()):
    """The REAL page, base.html and all — a version of this test that read the template as text
    would pass over a row moved into a branch that never renders."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["dec"] = lambda v, n=1: f"{v:.{n}f}"
    env.filters["money"] = lambda v: f"€{v:.2f}"
    return env.get_template("map.html").render(
        t=lambda k: k, track=list(track), places=list(places), stations=list(stations),
        trips_shown=trips_shown, stations_top_n=stations_top_n,
        page="map", vehicle={}, request=_Richiesta())


def _ha_le_caselle(html: str) -> bool:
    return "api/settings/map-trip-count" in html and "api/settings/map-station-count" in html


# ── the way out stays on screen ──────────────────────────────────────────────

def test_the_boxes_are_still_there_when_the_filter_empties_the_map():
    """The defect in one line: «Trips shown» = 1 draws nothing, and the box that says 1 is gone."""
    html = _render(track=[], trips_shown=1)
    assert _ha_le_caselle(html), "the only way to undo the setting vanished with the map"


def test_a_fresh_install_keeps_them_too():
    """Nothing driven yet, no filter set: the boxes are preferences and cost nothing to show, so
    they render here as well — the reporter asked for «always visible» and that is the whole of it."""
    assert _ha_le_caselle(_render(track=[], trips_shown=0))


def test_the_boxes_are_unchanged_when_there_is_a_map():
    """No regression for everybody who has a map today."""
    html = _render(track=UN_VIAGGIO, trips_shown=0)
    assert _ha_le_caselle(html)
    assert 'value="0"' in html          # the box still shows the stored value
    assert 'value="15"' in html


# ── the empty state says which emptiness it is ───────────────────────────────

def test_it_does_not_say_drive_when_a_filter_is_what_hid_the_map():
    html = _render(track=[], trips_shown=1)
    assert "map_no_data_filtered" in html, "still telling them to go and drive"
    assert "map_no_data\"" not in html and ">map_no_data<" not in html


def test_with_no_filter_it_still_says_there_is_no_data_yet():
    """The fresh-install wording is right and must not be traded away for the new one."""
    html = _render(track=[], trips_shown=0)
    assert "map_no_data_filtered" not in html
    assert "map_no_data" in html


def test_every_language_carries_the_new_line():
    """A missing key is not a crash, it is the English string leaking into seven languages.
    → tests/test_translations_complete.py counts them all; this one names the one added here."""
    import json
    loc = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    for f in sorted(loc.glob("*.json")):
        strings = json.loads(f.read_text())["translations"]
        assert "map_no_data_filtered" in strings, f"{f.name} has no filtered-map message"


# ── the counters describe the map, so they stay with it ──────────────────────

def test_the_legend_counters_do_not_show_up_over_an_empty_card():
    """«Routes (0)» and «— GPS points» under a 🌍 placeholder would be noise, and on a fresh
    install a lie about what the page is."""
    html = _render(track=[], trips_shown=0)
    assert "map_routes" not in html and "map_points" not in html


def test_the_counters_are_there_when_the_map_is():
    html = _render(track=UN_VIAGGIO, trips_shown=0)
    assert "map_routes" in html and "map_points" in html


def test_leaflet_never_runs_without_a_single_point():
    """The guard on this very edit: the row had to come OUT of the branch, the map script had to
    stay IN it. `L.latLngBounds([])` on an empty array is an exception, and an uncaught exception
    there kills the boxes just as dead as hiding them did."""
    assert "L.map(" not in _render(track=[], trips_shown=1)
    assert "L.map(" in _render(track=UN_VIAGGIO, trips_shown=0)


# ── found on the same row while verifying the above, and separate from it ────

# Two trips: one drawn in two runs bridged by a signal-loss gap, one plain. Nine points in all.
DUE_VIAGGI = [
    [{"points": [[45.46, 9.19], [45.47, 9.20]], "gap": False},
     {"points": [[45.47, 9.20], [45.52, 9.28]], "gap": True},
     {"points": [[45.52, 9.28], [45.53, 9.29], [45.54, 9.30]], "gap": False}],
    [{"points": [[45.10, 9.00], [45.11, 9.01]], "gap": False}],
]


def test_the_gps_point_counter_actually_counts():
    """«GPS points» has shown a dash on every map ever drawn, and it is not this change that broke
    it: at v3.14.26 as released the line that filled it (`document.getElementById('map-pts')`, in
    the map script) runs at parse time, while the element it looks for appears twenty lines FURTHER
    DOWN the document — so it got null, the `if (pc)` guard swallowed it, and the dash stayed.
    Verified by rendering the released tag's own template, and by opening the same seeded database
    through both versions side by side.

    Counting in the template is the same number — the script summed exactly these runs — and it
    needs neither a browser nor Leaflet to be right.
    """
    assert '<span id="map-pts">2</span>' in _render(track=UN_VIAGGIO, trips_shown=0)


def test_it_counts_across_runs_and_trips_the_way_the_script_did():
    """The gap-bridge run's points counted too: the script pushed every point of every run, and
    the number under the map has to keep meaning the same thing."""
    assert '<span id="map-pts">9</span>' in _render(track=DUE_VIAGGI, trips_shown=0)
