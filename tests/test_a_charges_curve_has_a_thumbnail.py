"""The thumbnail beside a charge's row: two small frames one under the other, the power above, the SoC below,
on one time axis, both over the big chart's scales, so the small picture has the big one's proportions, each
frame saying its unit (kW, %) where its line is not; a pixel column keeps its extremes, so a dip one sample
wide survives, and a lone reading holds the axis and the scale without a line. A computer's row asks for a
wider one. A pause is a hole in both lines, as the big chart draws it; a charge without telemetry gets a
dashed baseline in both frames, so the two cannot be mistaken for each other. A typed-in charge shows ✎
instead, a reconstructed one ✨. A merged charge's thumbnail covers both pieces, and its picture's address
changes with the merge, so a browser's cached picture of the single piece is not shown for the merged one;
an empty picture is not cached.

Charge 1 (3 July): an hour at 7 kW, 40 → 60 %, a sample every five minutes.
Charge 2 (4 July): twenty minutes, a pause of an hour, twenty minutes more.
Charge 3 (5 July): no samples at all. Charge 4 (6 July): typed in. Charge 5 (6 July): reconstructed.
Charges 6 and 7 (7 July): one plug-in in two pieces, ten minutes apart.
"""
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

import main
from test_a_days_heading_sums_up_its_trips import _client

_CHARGE = ("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,"
           " duration_min, charge_type, location_type, manual_entry, reconstructed) VALUES (?,1,?,?,?,?,?,?,'AC','HOME',?,?)")
_SAMPLE = ("INSERT INTO positions (vehicle_id, recorded_at, charging, charge_voltage_v, charge_current_a, soc)"
           " VALUES (1,?,1,400,?,?)")


def _at(day, hour, minute=0):
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'UTC')")

    def charge(cid, start, end, s0, s1, kwh, manual=0, reconstructed=0):
        c.execute(_CHARGE, (cid, start.isoformat(), end.isoformat(), s0, s1, kwh,
                            (end - start).total_seconds() / 60, manual, reconstructed))

    def samples(start, minutes, s0, s1, amps=-17.5):
        n = minutes // 5
        for k in range(n + 1):
            c.execute(_SAMPLE, ((start + timedelta(minutes=5 * k)).isoformat(), amps, s0 + (s1 - s0) * k / n))

    charge(1, _at(3, 8), _at(3, 9), 40, 60, 7.0)
    samples(_at(3, 8), 60, 40, 60)
    charge(2, _at(4, 8), _at(4, 9, 40), 30, 50, 5.0)
    samples(_at(4, 8), 20, 30, 40)
    samples(_at(4, 9, 20), 20, 40, 50)
    charge(3, _at(5, 8), _at(5, 9), 20, 40, 5.0)
    charge(4, _at(6, 8), _at(6, 9), 20, 40, 5.0, manual=1)
    charge(5, _at(6, 10), _at(6, 11), 20, 40, 5.0, reconstructed=1)
    charge(6, _at(7, 8), _at(7, 8, 30), 20, 30, 3.0)
    samples(_at(7, 8), 30, 20, 30)
    charge(7, _at(7, 8, 40), _at(7, 9, 10), 30, 40, 3.0)
    samples(_at(7, 8, 40), 30, 30, 40)
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete', '1')")   # or the page is the wizard
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return _client()


def _svg(client, cid):
    r = client.get(f"/charges/{cid}/curve.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    return r


def _lines(svg, colour):
    """Each drawn run of a series, as its points."""
    return [[tuple(map(float, p.split())) for p in re.findall(r"[ML]([\d.]+ [\d.]+)", d)]
            for d in re.findall(rf'<path d="([^"]+)" fill="none" stroke="{colour}"', svg)]


def _frames(svg):
    return [(float(y), float(h)) for y, h in re.findall(r'<rect x="0.5" y="([\d.]+)" width="\d+" height="([\d.]+)"', svg)]


def test_two_frames_power_above_soc_below(client):
    svg = _svg(client, 1).text
    (top_y, top_h), (bot_y, bot_h) = _frames(svg)
    assert top_y == 0.5 and bot_y > top_y + top_h, "the SoC frame sits under the power frame"
    (power,) = _lines(svg, "#fbbf24")
    (soc,) = _lines(svg, "#22c55e")
    assert all(top_y <= y <= top_y + top_h for _, y in power) and all(bot_y <= y <= bot_y + bot_h for _, y in soc)
    assert min(y for _, y in power) == pytest.approx(4 + 22 / 8, abs=0.1), "7 kW on the big chart's scale 0..8"
    assert len({y for _, y in power}) == 1, "a steady 7 kW is a flat line"
    assert soc[0][1] > soc[-1][1], "the SoC rises: its line climbs from left to right"
    assert soc[0][0] == power[0][0] == 4.0 and soc[-1][0] == power[-1][0] == 80.0, "one time axis, the frame's width"


def test_the_soc_line_ends_where_the_charge_began_and_ended(client):
    """40 → 60 % on the big chart's scale for those readings, 40..60: the line spans the frame's height."""
    (soc,) = _lines(_svg(client, 1).text, "#22c55e")
    _, (bot_y, bot_h) = _frames(_svg(client, 1).text)
    assert soc[0][1] == pytest.approx(bot_y - 0.5 + bot_h + 1 - 4, abs=0.6)       # 40 %: the bottom of the scale
    assert soc[-1][1] == pytest.approx(bot_y - 0.5 + 4, abs=0.6)                 # 60 %: the top


def test_a_pause_is_a_hole_in_both_lines(client):
    svg = _svg(client, 2).text
    power, soc = _lines(svg, "#fbbf24"), _lines(svg, "#22c55e")
    assert len(power) == 2 and len(soc) == 2, "two runs each, nothing drawn across the hour"
    assert power[0][-1][0] < power[1][0][0] and soc[0][-1][0] < soc[1][0][0]
    assert power[1][0][0] - power[0][-1][0] > 30, "the hole is the hour, three quarters of the axis"


def test_no_telemetry_is_a_dashed_baseline_in_both_frames(client):
    svg = _svg(client, 3).text
    assert not _lines(svg, "#fbbf24") and not _lines(svg, "#22c55e")
    assert svg.count('stroke-dasharray="3 3"') == 2
    assert svg != _svg(client, 2).text and 'stroke-dasharray' not in _svg(client, 1).text


def test_a_merged_charge_covers_both_pieces(client):
    before = _svg(client, 6).text
    assert db_reader.merge_charges(6, 7)["ok"]
    after = _svg(client, 6).text
    (p_before,) = _lines(before, "#fbbf24")
    runs = _lines(after, "#fbbf24")
    assert len(p_before) < sum(len(r) for r in runs), "the merged curve has the second piece's samples too"
    (soc_first, soc_last) = (_lines(after, "#22c55e")[0][0], _lines(after, "#22c55e")[-1][-1])
    assert soc_first[1] > soc_last[1], "from 20 % at the start of the first piece to 40 % at the end of the second"


def test_the_rows_picture_changes_with_a_merge(client):
    def src(cid, day):
        html = client.get("/api/charges/calendar/day", params={"year": 2026, "month": 7, "day": day}).text
        return re.search(rf'<img class="charge-thumb"[^>]*src="charges/{cid}/curve.svg\?v=([^"]+)"', html).group(1)
    one = src(6, 7)
    db_reader.merge_charges(6, 7)
    merged = src(6, 7)
    assert merged != one, "a browser's cached picture of the single piece would be shown for the merged charge"
    db_reader.unmerge_charges(6)
    assert src(6, 7) == one, "split again, the picture is the one it had"


def test_a_typed_in_charge_has_a_pencil_and_a_reconstructed_one_a_sparkle(client):
    html = client.get("/api/charges/calendar/day", params={"year": 2026, "month": 7, "day": 6}).text
    card4 = re.search(r'<details data-charge-id="4".*?</summary>', html, re.DOTALL).group(0)
    card5 = re.search(r'<details data-charge-id="5".*?</summary>', html, re.DOTALL).group(0)
    assert "charges/4/curve.svg" not in card4 and ">✎</div>" in card4
    assert "charges/5/curve.svg" not in card5 and ">✨</div>" in card5
    html = client.get("/api/charges/calendar/day", params={"year": 2026, "month": 7, "day": 3}).text
    assert 'loading="lazy" alt="" src="charges/1/curve.svg?v=' in html


def test_a_finished_charge_is_cached_for_a_day_a_running_or_empty_one_not(client):
    assert _svg(client, 1).headers["cache-control"] == "public, max-age=86400"
    assert _svg(client, 3).headers["cache-control"] == "no-store", "an empty picture may be another car's"
    import sqlite3
    with sqlite3.connect(db_reader.DB_PATH) as c:
        c.execute("UPDATE charges SET ended_at = NULL WHERE id = 1")
    assert _svg(client, 1).headers["cache-control"] == "no-store"


def test_a_pixel_column_keeps_its_extremes_and_a_hole_stays_open():
    """A long charge is drawn with at most four points a pixel column, its first, lowest, highest and last
    sample, so a one-sample dip to 0 kW reaches the bottom of the scale as it does on the big chart; the
    hole between two runs is never bridged."""
    times = [(_at(3, 8) + timedelta(minutes=k)).isoformat() for k in range(120)]
    times = times[:50] + times[90:]                     # a forty-minute hole after fifty minutes
    power = [7.0] * len(times)
    power[25] = 0.0
    soc = [40 + k * 0.2 for k in range(len(times))]
    runs = main._curve_runs(times, power)
    assert [len(r) for r in runs] == [50, 30]
    svg = main._curve_svg({"times": times, "power": power, "soc": soc})
    drawn = _lines(svg, "#fbbf24")
    assert len(drawn) == 2 and drawn[0][-1][0] < drawn[1][0][0]
    (top_y, top_h), _ = _frames(svg)
    assert max(y for run in drawn for _, y in run) == pytest.approx(top_y - 0.5 + top_h + 1 - 4, abs=0.1), "the dip reaches 0 kW"
    from collections import Counter
    assert max(Counter(int(x) for run in drawn for x, _ in run).values()) <= 4


def test_the_scales_are_the_big_charts():
    """139.5 kW sits at 139.5/160 of the power frame, as on the big chart, not at its top; a SoC that reaches
    100 % has 100 at the top of its scale and the scale's span below it."""
    times = [(_at(3, 8) + timedelta(minutes=5 * k)).isoformat() for k in range(5)]
    svg = main._curve_svg({"times": times, "power": [70, 139.5, 139.5, 100, 20], "soc": [87.2, 90, 95, 98, 100]})
    (top_y, top_h), (bot_y, bot_h) = _frames(svg)
    (power,) = _lines(svg, "#fbbf24")
    assert min(y for _, y in power) == pytest.approx(4 + (1 - 139.5 / 160) * (top_h + 1 - 8), abs=0.1)
    (soc,) = _lines(svg, "#22c55e")
    assert soc[-1][1] == pytest.approx(bot_y - 0.5 + 4, abs=0.1), "100 % at the top"
    assert soc[0][1] == pytest.approx(bot_y - 0.5 + 4 + (100 - 87.2) / 16 * (bot_h + 1 - 8), abs=0.1), "87.2 % on 84..100"


def test_a_computers_row_asks_for_a_wider_picture(client):
    html = client.get("/api/charges/calendar/day", params={"year": 2026, "month": 7, "day": 3}).text
    assert re.search(r'<source media="\(min-width: 640px\)" srcset="charges/1/curve.svg\?w=120&v=[^"]+">\s*'
                     r'<img class="charge-thumb" loading="lazy" alt="" src="charges/1/curve.svg\?v=', html)
    wide = client.get("/charges/1/curve.svg", params={"w": 120}).text
    assert 'viewBox="0 0 120 64"' in wide and 'width="119"' in wide
    (power,) = _lines(wide, "#fbbf24")
    assert power[0][0] == 4.0 and power[-1][0] == 116.0, "the time axis fills the wider frame"
    assert client.get("/charges/1/curve.svg", params={"w": 1000}).status_code == 422


def test_a_lone_reading_after_a_gap_holds_the_axis_and_the_scale():
    """Five readings at 7 kW, then one at 20 kW three hours later: no line to it, but the line before it ends
    early on the time axis and sits on the scale 0..20, as on the big chart."""
    times = [(_at(3, 8) + timedelta(minutes=5 * k)).isoformat() for k in range(5)] + [_at(3, 11, 20).isoformat()]
    svg = main._curve_svg({"times": times, "power": [7.0] * 5 + [20.0], "soc": [40, 41, 42, 43, 44, 45]})
    (power,) = _lines(svg, "#fbbf24")
    (top_y, top_h), _ = _frames(svg)
    assert power[-1][0] == pytest.approx(4 + 20 / 200 * 76, abs=0.1), "the axis runs on to the lone reading"
    assert power[0][1] == pytest.approx(4 + (1 - 7 / 20) * (top_h + 1 - 8), abs=0.1), "7 kW on the scale 0..20"


def test_a_missing_reading_breaks_the_line_and_is_never_a_zero():
    times = [(_at(3, 8) + timedelta(minutes=5 * k)).isoformat() for k in range(7)]
    svg = main._curve_svg({"times": times, "power": [7.0] * 7, "soc": [40, 41, None, 43, 44, 45, 46]})
    soc = _lines(svg, "#22c55e")
    assert len(soc) == 2, "the line breaks at the missing reading"
    (_, (bot_y, bot_h)) = _frames(svg)
    top, bottom = bot_y - 0.5 + 4, bot_y - 0.5 + bot_h + 1 - 4          # the scale's span inside the frame
    assert all(top <= y <= bottom for run in soc for _, y in run), "every point is on the 40..48 % scale: none is a 0"
    assert soc[1][0][1] < soc[0][-1][1], "after the hole the line goes on climbing"


def _units(svg):
    return [(m[3], m[2], float(m[0]), float(m[1]), m[4]) for m in re.findall(
        r'<text x="([\d.]+)" y="([\d.]+)" text-anchor="(\w+)"[^>]*fill="(#\w+)"[^>]*>([^<]+)</text>', svg)]


@pytest.mark.parametrize("cid", [1, 2])
def test_each_frame_says_its_unit_where_its_line_is_not(client, cid):
    """kW over the power, % over the SoC, in the series' colours, each in a corner of its own frame that its
    line does not cross, and drawn before the line, so a line is never covered."""
    svg = _svg(client, cid).text
    frames = _frames(svg)
    units = _units(svg)
    assert [(colour, text) for colour, _, _, _, text in units] == [("#fbbf24", "kW"), ("#22c55e", "%")]
    for (colour, anchor, x, y, text), (top, height) in zip(units, frames):
        assert top < y - 8 and y < top + height, (text, "inside its own frame")
        width = 11 if text == "kW" else 7
        x0 = x if anchor == "start" else x - width
        points = [p for run in _lines(svg, colour) for p in run]
        assert not [p for p in points if x0 - 1 <= p[0] <= x0 + width + 1 and y - 8 <= p[1] <= y + 1], (text, units)
        assert svg.index(f">{text}</text>") < svg.index(f'stroke="{colour}" stroke-width="2"'), "under the line"


def test_a_steady_power_near_the_top_sends_kw_to_the_bottom(client):
    """7 kW on 0..8 runs along the top of the frame, so the unit goes below it."""
    svg = _svg(client, 1).text
    (_, _, _, y, _), _ = _units(svg)
    (top, height), _ = _frames(svg)
    assert y > top + height / 2


def test_no_telemetry_no_units(client):
    assert "<text" not in _svg(client, 3).text
