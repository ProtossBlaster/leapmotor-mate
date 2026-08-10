"""A standby chart that has had nothing to draw must say so, instead of looking broken.

@riri19 (#241, 10/08/2026): *"the data seems to be stuck/frozen since 05/08"*. It was not stuck.
Mate had measured every stop since; each one had simply lost 0.1% — one step of the car's charge
sensor — and a drop that small cannot be told apart from noise, so no bar was drawn. Reading his
bundle answered it in a minute. Reading his screen could not, because the page says nothing at all
about the stops it discarded: he saw a chart that stopped growing, and from there it looks broken.

`get_vampire_drain` already returns `rejected` (each with hours, drop and why) and the battery route
already receives them — added in v3.10.2 for the bundle. Nothing printed them. This is the same
shape as the placeholder that never resolves: an absence rendered as nothing at all.

AND THE THRESHOLD WAS MISLABELLED. Two strings on that page, in all seven languages, call it a
`%/day` threshold. It is not a rate: `get_vampire_drain` compares the raw drop in SoC points
(`drop = soc0 - soc_end`) against it, so a 12-hour stop and a 3-day stop face the very same 0.2.
Calling it "per day" invites exactly the wrong fix — raising it to catch a slow drain. The bundle
carries the same wrong unit in two places, which is where WE read it during triage.

CI-safe apart from the route test, which skips without fastapi.
"""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
LOCALES = WEB / "locales"
LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl")

# How each language writes "per day" — the mislabel we are removing.
PER_DAY = ("/day", "/giorno", "/jour", "/Tag", "/dzień", "/dia", "/dag")


def _strings(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text())


def _find(d, key):
    """The locale files nest; the keys are unique, so walk to the first match."""
    for k, v in d.items():
        if isinstance(v, dict):
            hit = _find(v, key)
            if hit is not None:
                return hit
        elif k == key:
            return v
    return None


# ── 1) the threshold is a drop, and no language may call it a rate ──────────────
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ("battery_vampire_below_hint", "battery_vampire_more_below"))
def test_no_language_calls_the_noise_floor_a_daily_rate(lang, key):
    text = _find(_strings(lang), key)
    assert text is not None, f"{lang} is missing {key}"
    for unit in PER_DAY:
        assert unit not in text, (
            f"{lang}/{key} calls the noise floor a per-day rate: it is a drop in SoC points, "
            f"compared as-is however long the stop lasted")


def test_the_bundle_does_not_call_it_a_daily_rate_either():
    """Two lines in diagnostics.py print the same wrong unit — and the bundle is where we read it
    when we are the ones doing the triage."""
    src = (WEB / "diagnostics.py").read_text()
    for line in src.splitlines():
        if "min_drop" in line and "%" in line:
            assert "%/day" not in line, f"still mislabelled: {line.strip()}"


# ── 2) every language carries the new strings ──────────────────────────────────
NEW_KEYS = ("battery_vampire_why_none", "battery_vampire_why_noise",
            "battery_vampire_why_flat", "battery_vampire_why_short",
            "battery_vampire_why_woke")


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", NEW_KEYS)
def test_every_language_explains_the_empty_chart(lang, key):
    assert _find(_strings(lang), key), f"{lang} is missing {key}"


# ── 3) the page itself ─────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    """Minimal Starlette Request stand-in. `base.html` reads the ingress header to build its URLs,
    so a bare object is not enough — the render dies on the layout, not on the card."""
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _seed(tmp_path, monkeypatch, *, last_park_is_rejected: bool):
    """Two stops: one worth a bar, one that lost a single sensor step. `last_park_is_rejected`
    decides which of the two happened most recently — the whole question the line answers."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")

    now = datetime.now(timezone.utc)

    def park(start_h, end_h, soc0, soc1, odo):
        """Half-hourly OFF samples, SoC falling linearly from soc0 to soc1."""
        steps = int((start_h - end_h) * 2)
        for i in range(steps + 1):
            t = now - timedelta(hours=start_h) + timedelta(minutes=30 * i)
            soc = soc0 + (soc1 - soc0) * (i / steps if steps else 1)
            c.execute(
                "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
                " odometer_km, ready) VALUES (1,?,?,0,0,?,0)",
                (t.isoformat(), round(soc, 1), odo))

    def drive(at_h, soc, odo_to):
        """One moving sample, so the stops either side are separate windows.

        ⚠️ Its SoC must MATCH the end of the stop before it. A lower one is read as the first fresh
        reading after a sleep and closes that stop on it — which is right on a real car, and here
        invented a 15-point drop that turned a discarded stop into a charted bar. The fixture was
        wrong, not the code."""
        t = now - timedelta(hours=at_h)
        c.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
            " odometer_km, ready) VALUES (1,?,?,0,50,?,1)",
            (t.isoformat(), soc, odo_to))

    if last_park_is_rejected:
        # TWO rejected stops, with clearly different lengths. With only one, "the oldest" and "the
        # most recent" are the same row and the page could pick either — a mutation swapping them
        # survived on exactly that fixture.
        park(140, 120, 90.0, 89.9, 1000)    # 20 h, −0.1% → rejected, and it is the OLD one
        drive(119, 89.9, 1000)
        park(80, 50, 80.0, 79.0, 1000)      # 30 h, −1.0% → a bar
        drive(49, 79.0, 1030)
        park(14, 0.5, 70.0, 69.9, 1030)     # 13.5 h, −0.1% → rejected, and it is the LATEST
    else:
        park(80, 66, 70.0, 69.9, 1000)      # 14 h, −0.1% → rejected
        drive(65, 69.9, 1030)
        park(50, 0.5, 80.0, 79.0, 1030)     # 49.5 h, −1.0% → a bar, and it is the latest
    pdb._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _render(tmp_path, monkeypatch, **kw):
    import asyncio
    import db_reader
    _seed(tmp_path, monkeypatch, **kw)
    import main
    # main imported db_reader by name; point its copy at the same seeded file.
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    # asyncio.run, NOT get_event_loop: the latter borrows whatever loop the rest of the suite left
    # behind, so this file passed on its own and failed inside the suite — the exact shape of a
    # test that is green for a reason other than the code being right.
    return asyncio.run(main.battery_page(_Req())).body.decode()


def _note(body):
    """Just the explanation line, so an assertion cannot be satisfied by a number that happens to
    appear elsewhere on a page full of numbers."""
    import re
    m = re.search(r'id="vampire-why-none"[^>]*>(.*?)</p>', body, re.S)
    return " ".join(m.group(1).split()) if m else ""


def test_the_page_says_why_there_is_no_new_bar(tmp_path, monkeypatch):
    """The most recent stop was discarded → the page must name THAT one, with its length and drop."""
    note = _note(_render(tmp_path, monkeypatch, last_park_is_rejected=True))
    assert note, "no explanation line at all"
    assert "13.5" in note, f"not the most recent stop: {note}"
    assert "0.1" in note, f"its drop is missing: {note}"
    assert "20" not in note, f"it named the OLDER discarded stop: {note}"


def test_the_numbers_follow_the_language(tmp_path, monkeypatch):
    """Italian writes 13,5. The headline on this very card already does — a new line that says
    13.5 beside a figure that says 0,5 is one page disagreeing with itself, and it is the kind of
    thing that reads as a bug in the number rather than in the formatting."""
    import db_reader
    _seed(tmp_path, monkeypatch, last_park_is_rejected=True)
    db_reader.set_setting("language", "it")

    import asyncio
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    note = _note(asyncio.run(main.battery_page(_Req())).body.decode())

    assert "13,5" in note, f"the duration is not written the Italian way: {note}"
    assert "0,1" in note, f"the drop is not written the Italian way: {note}"


def test_nothing_is_said_when_the_latest_stop_is_charted(tmp_path, monkeypatch):
    """The line answers 'why is there no new bar'. When there IS one, it must keep quiet —
    otherwise it becomes furniture and stops being read."""
    import db_reader
    body = _render(tmp_path, monkeypatch, last_park_is_rejected=False)
    v = db_reader.get_vampire_drain(min_drop_pct=0.2, min_hours=1.0)
    assert v["windows"], "the fixture is wrong: the latest stop should have produced a bar"
    assert "vampire-why-none" not in body
