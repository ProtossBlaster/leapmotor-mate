"""On a range extender the trip's energy tile shows getEC — nothing derived from SoC (beta #11).

@michapr, 05/08/26: *"Since I've realized that the SoC difference shouldn't matter for calculating
travel costs — and given that we always use getEC — I don't think we need to display that value here
anymore. To avoid confusion, we should only show the getEC value that is actually used for the
calculation. In my case, that's 2.0 kWh (which is where the €0.50 in the image comes from)."*

Both branches the tile used to choose between are SoC arithmetic:

  * `battery_net_kwh` — ΔSoC × capacity, computed on read (db_reader.get_trip_detail);
  * `energy_kwh`      — efficiency × distance, where the efficiency itself was
                        `(start_soc − end_soc) / 100 × capacity / km` when the trip closed
                        (poller/db.py::finish_trip).

On a series hybrid the generator refills the pack mid-drive, so neither measures what the driving
used. And neither is what the money printed beside them is billed on: a range-extender trip is
priced by drawing the paid stock down by `ec_kwh` (reev_trip_electric_cost). One tile, one number,
and it is the one the cost agrees with.

Silvio's rule behind it, the same day: *«sulle REEV non possiamo basarci sul SoC perché varia e sale
e scende in base se parte o meno il generatore»*.
"""
import pathlib
import re

import pytest

import db as D
import db_reader

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "templates" / "trip_detail.html"

LABELS = {"battery_net": "Battery change", "energy_used": "Energy used"}


def _trip(tmp_path, monkeypatch, *, start_soc, end_soc, eff=None, ec=None, capacity="67.1"):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " efficiency_kwh_100km, start_soc, end_soc, ec_kwh) VALUES (1,1,?,?,129.0,?,?,?,?)",
        ("2026-07-28T10:00:00+00:00", "2026-07-28T12:00:00+00:00", eff, start_soc, end_soc, ec))
    pdb._conn.commit()
    db_reader.set_setting("battery_capacity_kwh", capacity)
    return db_reader.get_trip_detail(1)


def _render_tile(trip, *, is_reev):
    """Render the real tile out of the real template — a hand-written stand-in cannot fail on a
    guard that lives in Jinja."""
    jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")
    src = TEMPLATE.read_text()
    start = src.index("{% if is_reev %}")
    end = src.index("{% endif %}\n        </div>", start) + len("{% endif %}")  # from `start`:
    # that marker also closes an earlier tile, and slicing on the first hit yields an empty block
    # that renders to "" — every assertion below would then compare "" to "" and pass.
    env = jinja2.Environment()
    env.filters["dec"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f}"
    out = env.from_string(src[start:end]).render(
        trip=trip, is_reev=is_reev, t=lambda k: LABELS[k])
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out)).strip()


# ── the range extender ────────────────────────────────────────────────────────
def test_a_reev_trip_shows_the_getec_figure(tmp_path, monkeypatch):
    """His 28 July trip: 2.0 kWh metered, and that is the number the 0.50 € came from."""
    trip = _trip(tmp_path, monkeypatch, start_soc=80.0, end_soc=55.0, eff=20.0, ec=2.0)
    assert _render_tile(trip, is_reev=True) == "Energy used 2.0 kWh"


def test_a_reev_trip_never_shows_the_soc_derived_energy(tmp_path, monkeypatch):
    """The SoC pair here is worth 16.8 kWh and the efficiency figure 25.8 — both would be printed by
    the old tile, and neither is what the trip was billed on."""
    trip = _trip(tmp_path, monkeypatch, start_soc=80.0, end_soc=55.0, eff=20.0, ec=2.0)
    assert trip["energy_kwh"] == pytest.approx(25.8)      # still computed — the BEV cost path uses it
    visible = _render_tile(trip, is_reev=True)
    assert "25.8" not in visible and "16.8" not in visible, visible


def test_a_reev_trip_that_ended_fuller_shows_getec_not_the_gain(tmp_path, monkeypatch):
    """The generator handing the pack more than the motor took is exactly when the SoC number is at
    its most misleading: it reads as a gain while the drive really did consume 2 kWh."""
    trip = _trip(tmp_path, monkeypatch, start_soc=69.7, end_soc=74.0, ec=2.0)
    assert trip["battery_net_kwh"] == pytest.approx(-2.89, abs=0.01)   # still derived, just not shown
    visible = _render_tile(trip, is_reev=True)
    assert visible == "Energy used 2.0 kWh"
    assert "Battery change" not in visible


def test_a_reev_trip_with_no_reading_yet_shows_a_dash_not_a_substitute(tmp_path, monkeypatch):
    """getEC arrives with a later poll. Until it does the tile says we do not know — it must not
    quietly fall back to the SoC figure, which is the whole defect wearing a fallback."""
    trip = _trip(tmp_path, monkeypatch, start_soc=80.0, end_soc=55.0, eff=20.0, ec=None)
    visible = _render_tile(trip, is_reev=True)
    assert visible == "Energy used —", visible


def test_a_reev_reading_of_zero_is_a_reading(tmp_path, monkeypatch):
    """0.0 kWh out of the pack on a trip the generator drove is a fact, not a missing value. It
    prints as a dash today — recorded here so the behaviour is a decision, not an accident."""
    trip = _trip(tmp_path, monkeypatch, start_soc=80.0, end_soc=80.0, ec=0.0)
    assert _render_tile(trip, is_reev=True) == "Energy used —"


# ── the plain electric car is untouched ───────────────────────────────────────
def test_a_bev_still_shows_its_consumption(tmp_path, monkeypatch):
    trip = _trip(tmp_path, monkeypatch, start_soc=80.0, end_soc=55.0, eff=20.0, ec=2.0)
    assert _render_tile(trip, is_reev=False) == "Energy used 25.8 kWh"


def test_a_bev_that_ended_fuller_still_shows_the_net_gain(tmp_path, monkeypatch):
    """The v3.6.x behaviour these two asked for in the first place stays exactly as it was."""
    trip = _trip(tmp_path, monkeypatch, start_soc=69.7, end_soc=74.0)
    visible = _render_tile(trip, is_reev=False)
    assert "Battery change" in visible and "-2.9 kWh" in visible, visible


def test_a_bev_with_nothing_to_show_still_shows_a_dash(tmp_path, monkeypatch):
    trip = _trip(tmp_path, monkeypatch, start_soc=80.0, end_soc=80.0)
    assert _render_tile(trip, is_reev=False) == "Energy used —"


# ── the page as a whole ───────────────────────────────────────────────────────
def test_the_whole_template_still_compiles():
    """A tile edit that leaves an unbalanced block takes the entire page down, and no assertion
    above would notice — every one of them renders a slice. Parsed, not rendered: the point is the
    block structure, and rendering would need the app's whole filter set to get there."""
    jinja2 = pytest.importorskip("jinja2", reason="needs jinja2")
    jinja2.Environment().parse(TEMPLATE.read_text())


def test_the_soc_branches_are_out_of_reach_on_a_reev():
    """Read on the source: the two SoC branches must sit behind `is_reev` being false. An edit that
    reorders them back could still satisfy the renders above on the sample data used there."""
    src = TEMPLATE.read_text()
    _a = src.index("{% if is_reev %}")
    tile = src[_a:src.index("{% endif %}\n        </div>", _a)]
    assert tile.index("{% if is_reev %}") < tile.index("{% elif trip.battery_net_kwh is not none %}")
    assert "trip.ec_kwh" in tile.split("{% elif")[0], "the REEV branch stopped reading getEC"
