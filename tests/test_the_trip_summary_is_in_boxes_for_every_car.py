"""The trip summary in boxes, on every car — beta D #31, approved by Silvio on 18/09/2026.

@michapr's card of 14/09 laid the summary out as boxes: distance and duration, then the electricity
and the fuel side by side, then the total. Silvio took the boxes, asked for the price per litre back,
and asked for the SAME card on a plain electric car, without the fuel. The driving-mode bar of that
card stays out: its electric share is a ceiling drawn as a fact (Silvio's no of 13/09).

Side by side was measured before it was taken, on the rendered page. At a 1024px window each box has
73px of room: "1.912 €/L" alone needs 43, "4.82 € · 1.912 €/L" on one line needs 83, and the
generator's distance with its words needs 112. Hence the price per litre on a line of its own, and
the generator's distance on a full-width line under the boxes. At that width the distance/duration
pair also wrapped at 24px ("112.0" over "km", "2h" over "03m"), so it steps down to 18px only inside
the narrow three-column layout — checked in a browser at 1024 and 1280.

Rendered, not grepped, with the same harness as the other trip-card tests; the structure is read
back with a small parser, because "the price per litre is on its own line" and "the generator's
distance is outside the boxes" are statements about where an element sits, not about a substring.
→ [[a-layout-question-is-not-answered-by-measuring-harder]] · [[feedback-a-green-test-can-assert-the-bug]]
"""
import json
import pathlib
from html.parser import HTMLParser

import jinja2
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"
LOCALES = ROOT / "web" / "locales"


class _Quiet(jinja2.Undefined):
    """The page drags in the whole chrome; what it does not need resolves to nothing. Every
    assertion below is on something that has to be DRAWN, so a failed lookup fails loudly."""
    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        return self

    def __getitem__(self, key):
        return self

    def __str__(self):
        return ""


class _Request:
    headers = {"x-ingress-path": ""}


def _reev_trip(**over):
    """A range-extender trip that burned petrol, with the generator's distance detected."""
    t = {"id": 7, "started_at": "2026-09-01T07:10:00+02:00", "ended_at": "2026-09-01T08:20:00+02:00",
         "distance_km": 97.0, "duration_min": 70.0, "start_soc": 50.0, "end_soc": 23.0,
         "ec_kwh": 4.8, "ec_stable": 1, "ec_driving": 4.0, "ec_ac": 0.5, "ec_other": 0.3,
         "efficiency_kwh_100km": None, "cost": 1.34, "fuel_cost": 11.14, "cost_total": 12.48,
         "cost_per_kwh": 0.279, "fuel_used_l": 5.28, "fuel_l_100km": 5.4, "engine_ran": True,
         "engine_km": 62.0, "fuel_start_pct": 30.8, "fuel_end_pct": 20.3, "reconstructed": 0,
         "positions": [], "start_odometer_km": 12000.0, "end_odometer_km": 12097.0,
         "avg_speed_kmh": 83.0, "max_speed_kmh": 130.0, "elevation_gain_m": None,
         "elevation_loss_m": None, "outside_temp_start_c": None, "outside_temp_end_c": None,
         "elevation_profile_available": False, "ec_pending": False, "paid_kwh": None,
         "free_kwh": None, "fuel_price_per_l": 1.829, "reev_elec_kwh_100km": 46.0,
         "battery_net_kwh": None, "energy_kwh": None, "regen_kwh": None}
    t.update(over)
    return t


def _ev_trip(**over):
    """A plain electric car: no tank, the SoC-based energy, a priced charge behind it."""
    t = _reev_trip(ec_kwh=None, efficiency_kwh_100km=15.1, energy_kwh=14.6, cost=3.10,
                   fuel_cost=None, cost_total=3.10, fuel_used_l=None, fuel_l_100km=None,
                   engine_ran=False, engine_km=None, fuel_start_pct=None, fuel_end_pct=None,
                   fuel_price_per_l=None, reev_elec_kwh_100km=None, regen_kwh=0.9)
    t.update(over)
    return t


def _env():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True,
                             undefined=_Quiet)
    env.filters["money"] = lambda v: "—" if v is None else f"{float(v):.2f} €"
    env.filters["price3"] = lambda v: "—" if v is None else f"{float(v):.3f}"
    env.filters["dec"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f}"
    env.filters["nice"] = lambda v: "—" if v is None else f"{float(v):g}"
    env.filters["dist"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f} km"
    env.filters["speed"] = lambda v, n=0: "—" if v is None else f"{float(v):.{n}f} km/h"
    env.filters["elev"] = lambda v, n=0: "—" if v is None else f"{float(v):.{n}f} m"
    env.filters["temp"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f} °C"
    env.filters["localdate"] = lambda v: "1 Sep 2026"
    env.globals["dist_unit"] = lambda: "km"
    env.globals["dist_val"] = lambda v, n=1: None if v is None else round(float(v), n)
    env.globals["dist100_unit"] = lambda: "100 km"
    env.globals["cost100_val"] = lambda v: v
    env.globals["eff_unit"] = lambda: "kWh/100km"
    env.globals["eff_val"] = lambda v, n=1: v
    env.globals["fmt_dur"] = lambda v: "1h 10m"
    return env


def _render(**ctx):
    base = dict(trip=_reev_trip(), t=lambda k, **kw: k, request=_Request(), page="trips",
                version="test", demo=False, is_reev=True, research=False,
                currency={"name": "Euro", "symbol": "€", "pos": "after", "dec": 2},
                prev_trip_id=None, next_trip_id=None)
    base.update(ctx)
    return _env().get_template("trip_detail.html").render(**base)


# ── a small tree, so "where does it sit" can be asked ──────────────────────────────────────────
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
         "track", "wbr"}


class _Node:
    def __init__(self, tag: str, attrs, parent: "_Node | None"):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children: list["_Node"] = []
        self.text = ""

    @property
    def cls(self):
        return self.attrs.get("class") or ""

    def all_text(self):
        return self.text + "".join(c.all_text() for c in self.children)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


class _Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", [], None)
        self.cur: _Node = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_endtag(self, tag):
        n: _Node | None = self.cur
        while n is not None and n is not self.root and n.tag != tag:
            n = n.parent
        if n is not None and n is not self.root and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.text += data


def _summary(html) -> _Node:
    """The first card of the page — the one that holds the trip summary."""
    tree = _Tree(html)
    for n in tree.root.walk():
        if n.tag == "div" and n.cls.split() == ["card"] and "trip_summary_title" in n.all_text():
            return n
    raise AssertionError("no card carries the trip summary title")


def _leaf_with(card: _Node, text) -> _Node:
    """The innermost element whose own text contains `text`."""
    hits = [n for n in card.walk() if text in n.text]
    assert hits, f"{text!r} is not drawn on the summary card"
    return hits[-1]


def _box_of(card: _Node, key) -> _Node:
    """The box (a direct child of the energy grid) that holds `key`."""
    node = _leaf_with(card, key)
    for a in node.ancestors():
        if a.parent is not None and "grid" in a.parent.cls.split() and a.tag == "div" \
                and "rounded-lg" in a.cls:
            return a
    raise AssertionError(f"{key!r} is not inside a box")


# ── the card ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("is_reev,trip", [(True, _reev_trip()), (False, _ev_trip())],
                         ids=["range-extender", "electric"])
def test_the_card_is_a_trip_summary_on_every_car(is_reev, trip):
    card = _summary(_render(is_reev=is_reev, trip=trip))
    for key in ("distance", "duration", "trip_area_electric", "total_cost"):
        assert key in card.all_text(), f"{key} is missing from the summary card"


def test_a_range_extender_shows_the_electricity_and_the_fuel_side_by_side():
    card = _summary(_render())
    elec, fuel = _box_of(card, "trip_area_electric"), _box_of(card, "trip_area_fuel")
    assert elec.parent is fuel.parent, "the two boxes are not in the same row"
    grid = elec.parent.cls.split()
    assert "grid-cols-2" in grid, "the electricity and the fuel are not side by side"
    assert not [c for c in grid if ":" in c and "grid-cols" in c], \
        "the pair is behind a viewport breakpoint — the card is a column of the page, measure it"


def test_an_electric_car_gets_the_same_card_without_the_fuel():
    html = _render(is_reev=False, trip=_ev_trip())
    card = _summary(html)
    assert "trip_area_fuel" not in card.all_text(), "a car with no tank shows a fuel box"
    elec = _box_of(card, "trip_area_electric")
    assert "grid-cols-1" in elec.parent.cls.split(), "the lone electricity box leaves an empty cell"


def test_the_price_per_litre_is_back_on_a_line_of_its_own():
    """Silvio, 18/09: the €/L back. On its own line because the box has 73px at 1024px and the
    cost and the price together need 83."""
    card = _summary(_render())
    line = _leaf_with(card, "1.829 €/L")
    assert "11.14" not in line.all_text(), "the price per litre shares a line with the fuel cost"
    assert _box_of(card, "1.829 €/L") is _box_of(card, "trip_area_fuel"), \
        "the price per litre is not in the fuel box"


def test_the_electricity_box_carries_its_own_price_and_cost_on_a_range_extender():
    card = _summary(_render())
    elec = _box_of(card, "trip_area_electric")
    assert "4.8" in elec.all_text(), "the getEC energy is not in the electricity box"
    assert "1.34 €" in elec.all_text(), "the electric half of the cost is not in its box"
    assert "0.279 €/kWh" in elec.all_text(), "the price the electricity was billed at is gone"


def test_on_an_electric_car_the_cost_is_printed_once():
    """With no fuel there is nothing to split: the electricity's cost IS the total. Printed in the
    box and in the total it would be the same figure twice in one card."""
    card = _summary(_render(is_reev=False, trip=_ev_trip()))
    assert card.all_text().count("3.10 €") == 1
    elec = _box_of(card, "trip_area_electric")
    for figure in ("14.6", "15.1", "0.279 €/kWh", "regen"):
        assert figure in elec.all_text(), f"{figure} is missing from the electricity box"


def test_the_generator_distance_keeps_its_floor_note_outside_the_boxes():
    """It is a floor, and it is printed with the words that say so wherever it appears (v3.14.24).
    No box has room for it at 1024px, so it gets a full-width line."""
    card = _summary(_render())
    label = _leaf_with(card, "reev_engine_km_label")
    assert not [a for a in label.ancestors() if a in (_box_of(card, "trip_area_fuel"),)], \
        "the generator's distance was squeezed into the fuel box"
    block = label.parent
    assert "reev_engine_km_floor" in block.all_text(), "the generator's distance lost its floor note"
    assert "62.0 km" in block.all_text()


def test_the_electric_rate_of_a_generator_trip_stays_off_the_official_build():
    """46 kWh/100km on a trip where the generator refilled the pack is not the car's consumption.
    Today's rule stands in the new card: the research line on the beta build, nothing elsewhere."""
    assert "46 kWh/100km" not in _summary(_render(research=False)).all_text()
    assert "46 kWh/100km" in _summary(_render(research=True)).all_text()


def test_the_driving_mode_bar_is_not_there():
    """Silvio's no of 13/09: the bar would draw distance minus a floor — a ceiling — as a fact."""
    html = _render()
    assert "driving_mode" not in html
    assert "split_unknown" not in html


def test_distance_and_duration_step_down_only_in_the_narrow_column():
    card = _summary(_render())
    for key, value in (("distance", "97.0"), ("duration", "1h 10m")):
        node = _leaf_with(card, value)
        classes = node.cls.split()
        for c in ("whitespace-nowrap", "text-2xl", "lg:text-lg", "xl:text-2xl"):
            assert c in classes, f"the {key} value lacks {c}"


def test_the_cost_per_distance_cannot_lose_its_unit():
    """Seen in the browser on the implementation, not on the English prototype: at a 1024px window
    the Italian "COSTO TOTALE" and the Polish "ŁĄCZNY KOSZT" take two lines, and the running cost
    beside them broke INSIDE the figure — "1,40 €/100" over "km", the orphaned unit of #199. The
    label may wrap; the figure may not."""
    card = _summary(_render())
    line = _leaf_with(card, "12.87 €/100 km")
    assert "whitespace-nowrap" in line.cls.split(), "the cost per distance can break inside itself"


@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_every_language_names_the_summary(path):
    text = json.loads(path.read_text(encoding="utf-8"))["translations"].get("trip_summary_title")
    assert text and text.strip(), f"{path.stem} has no title for the trip summary"
