"""beta #11 (@michapr, @gm27271): a trip that ends with MORE charge than it started had an empty
energy tile, and the number behind it was never stored.

The poller computes (start_soc − end_soc) × capacity at trip close and throws it away: it only
feeds kWh/100 km, which is withheld when the net is ≤ 0 and — decisively — whenever the
range-extender ran at all (beta #10, and rightly so: a SoC-derived consumption means nothing while
the pack is being refilled mid-drive). So the tile was not a suppressed value, it was nothing.

It is now derived on read from the stored SoC pair, so every trip already recorded has it without a
migration, and shown ONLY when the pack ended fuller — where the net is positive the consumption
figure beside it already says so, and two numbers under one word is its own defect.
"""
import pathlib

import pytest

import db as D
import db_reader

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _trip(tmp_path, monkeypatch, *, start_soc, end_soc, eff=None, capacity="67.1"):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " efficiency_kwh_100km, start_soc, end_soc) VALUES (1,1,?,?,20.0,?,?,?)",
        ("2026-07-28T10:00:00+00:00", "2026-07-28T10:30:00+00:00", eff, start_soc, end_soc))
    pdb._conn.commit()
    db_reader.set_setting("battery_capacity_kwh", capacity)
    return pdb


def test_a_trip_that_ended_fuller_reports_the_net_gain(tmp_path, monkeypatch):
    """The reported case: the generator put back more than the motor took. 4.3 points of a 67.1 kWh
    pack = 2.89 kWh gained, carried with the sign the two of them asked for."""
    _trip(tmp_path, monkeypatch, start_soc=69.7, end_soc=74.0)
    d = db_reader.get_trip_detail(1)
    assert d["battery_net_kwh"] == pytest.approx(-2.89, abs=0.01)


def test_an_ordinary_trip_carries_no_net_figure(tmp_path, monkeypatch):
    """Where the pack drained, the consumption tile already answers the question — the net must stay
    out, or the same trip shows two energy numbers under two labels."""
    _trip(tmp_path, monkeypatch, start_soc=80.0, end_soc=70.0, eff=30.0)
    d = db_reader.get_trip_detail(1)
    assert d["battery_net_kwh"] is None
    assert d["energy_kwh"] == pytest.approx(6.0)      # the usual figure is untouched


def test_a_flat_trip_carries_no_net_figure(tmp_path, monkeypatch):
    """Equal SoC is not a gain: strictly fuller, or nothing."""
    _trip(tmp_path, monkeypatch, start_soc=70.0, end_soc=70.0)
    assert db_reader.get_trip_detail(1)["battery_net_kwh"] is None


def test_the_net_is_not_the_gross_energy_that_left_the_pack(tmp_path, monkeypatch):
    """The distinction the label exists for. A trip can end fuller AND still have drawn energy: the
    official figure (efficiency × distance) is gross energy out and stays positive. Both are shown,
    and they are not interchangeable — printing a minus on the gross one would answer with the wrong
    quantity."""
    _trip(tmp_path, monkeypatch, start_soc=69.7, end_soc=74.0, eff=15.0)
    d = db_reader.get_trip_detail(1)
    assert d["battery_net_kwh"] == pytest.approx(-2.89, abs=0.01)   # pack gained
    assert d["energy_kwh"] == pytest.approx(3.0)                    # yet 3 kWh left it
    assert d["energy_kwh"] > 0


def test_the_tile_renders_the_net_instead_of_the_empty_consumption(tmp_path, monkeypatch):
    """Rendered from the real template — a fake renderer doesn't catch a guard that lives in Jinja."""
    jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")
    _trip(tmp_path, monkeypatch, start_soc=69.7, end_soc=74.0)
    trip = db_reader.get_trip_detail(1)
    src = (ROOT / "web" / "templates" / "trip_detail.html").read_text()
    # The tile now opens on `{% if is_reev %}` \u2014 the SoC branches moved behind it (beta #11, a
    # range-extender must show getEC and nothing derived from SoC). Rendered whole, with is_reev
    # false, so this keeps asserting the plain-electric behaviour it was written for.
    start = src.index("{% if is_reev %}")
    block = src[start:src.index("{% endif %}\n        </div>", start) + len("{% endif %}")]
    env = jinja2.Environment()
    env.filters["dec"] = lambda v, n=1: "\u2014" if v is None else f"{float(v):.{n}f}"
    out = env.from_string(block).render(
        trip=trip, is_reev=False,
        t=lambda k: {"battery_net": "Battery change", "energy_used": "Energy used"}[k])
    import re
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out)).strip()
    assert "Battery change" in visible
    assert "-2.9 kWh" in visible, f"the value/unit don't read as one figure: {visible!r}"
    assert "Energy used" not in visible, "the empty consumption label is still being printed alongside"


def test_the_key_exists_in_every_language(tmp_path, monkeypatch):
    """A new label reaches seven files or it silently falls back to English for five of them."""
    import json
    for f in sorted((ROOT / "web" / "locales").glob("*.json")):
        d = json.load(open(f))
        def find(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "battery_net":
                        return v
                    r = find(v)
                    if r:
                        return r
        val = find(d)
        assert val, f"battery_net missing from {f.name}"
        assert val != find(json.load(open(ROOT / "web" / "locales" / "en.json"))) or f.name == "en.json", \
            f"{f.name} still carries the English string"
