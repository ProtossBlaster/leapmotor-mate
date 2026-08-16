"""A trip that could be joined on both sides is drawn ONCE, not twice (#249, @riri19).

> *"When I activate the view for 'Mergeable' trips, I notice that some trips are duplicated and
> stacked… this repetition of the same trip cards creates an impression of clutter and redundancy."*

He was not misreading it. The view proposed **pairs**: for every two adjacent trips close enough
together it drew a card, the 🔗 connector, and the other card. When the trips form a CHAIN — six
errands one after another — every trip in the middle is the second of one pair and the first of the
next, so it is drawn twice.

Measured on the bundle he attached, at the 90-minute stop he had the slider set to: **23 pairs, 46
cards for 34 trips — 12 trips drawn twice**, and on 14 August his six trips of the day became ten
cards under a heading that said "6 trajets". At the default 5-minute stop the same data produces 4
pairs and no repetition at all, which is why the slider is half the story.

The chain is now one block: every trip once, a connector between each neighbouring pair. Nothing
changes underneath — a merge is still between two adjacent trips, and each connector still carries
exactly its own two.
"""
import datetime

import pytest

DAY = datetime.date(2026, 8, 14)


def _riri19_day(tmp_path, monkeypatch):
    """His 14 August: six trips in a row, each stop under 90 minutes, no charge in between."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    rows = [  # start, end, km, start_soc, end_soc  — the stops are 19', 13', 47', 62', 71'
        ("13:52", "14:23", 37.0, 78.3, 69.6),
        ("14:42", "14:46",  1.0, 69.6, 69.2),
        ("14:58", "15:05",  3.0, 69.2, 68.4),
        ("15:52", "16:00",  2.0, 67.2, 65.5),
        ("17:02", "17:11",  2.0, 65.0, 64.2),
        ("18:21", "18:57", 39.0, 64.1, 49.7),
    ]
    for i, (t0, t1, km, s0, s1) in enumerate(rows, start=1):
        c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
                  " start_soc, end_soc, efficiency_kwh_100km) VALUES (?,1,?,?,?,?,?,16.0)",
                  (i, f"2026-08-14T{t0}:00+00:00", f"2026-08-14T{t1}:00+00:00", km, s0, s1))
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','UTC')")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def test_the_whole_day_is_one_chain(tmp_path, monkeypatch):
    d = _riri19_day(tmp_path, monkeypatch)
    chains = d.get_merge_chains(90, day=DAY)
    assert len(chains) == 1
    assert len(chains[0]) == 6, "six trips, one chain"


def test_every_trip_appears_exactly_once(tmp_path, monkeypatch):
    """The defect, counted: five pairs used to draw ten cards for these six trips."""
    d = _riri19_day(tmp_path, monkeypatch)
    ids = [step["trip"]["id"] for chain in d.get_merge_chains(90, day=DAY) for step in chain]
    assert ids == sorted(set(ids)), f"a trip is drawn more than once: {ids}"
    assert len(ids) == 6


def test_each_connector_joins_its_own_two_neighbours(tmp_path, monkeypatch):
    """The merge stays pairwise: the link on a card names the card below it, and nothing else."""
    d = _riri19_day(tmp_path, monkeypatch)
    chain = d.get_merge_chains(90, day=DAY)[0]
    for step, nxt in zip(chain, chain[1:]):
        assert step["link"] is not None
        assert step["link"]["b_id"] == nxt["trip"]["id"]
    assert chain[-1]["link"] is None, "the last card has nothing to join below it"


def test_the_stops_are_the_ones_he_saw(tmp_path, monkeypatch):
    d = _riri19_day(tmp_path, monkeypatch)
    chain = d.get_merge_chains(90, day=DAY)[0]
    assert [s["link"]["gap_min"] for s in chain if s["link"]] == [19, 12, 47, 62, 70]


def test_a_shorter_stop_setting_breaks_the_chain_up(tmp_path, monkeypatch):
    """At the default 5 minutes his day has no eligible pair at all — which is why the slider is
    half the story, and why a fixture at the default would have proved nothing."""
    d = _riri19_day(tmp_path, monkeypatch)
    assert d.get_merge_chains(5, day=DAY) == []


def test_two_separate_pairs_stay_two_chains(tmp_path, monkeypatch):
    """Chaining must not glue together trips that are NOT adjacent: the 13' stop links its two,
    the 47' one links its own two, and with a 20-minute setting the middle link is gone."""
    d = _riri19_day(tmp_path, monkeypatch)
    chains = d.get_merge_chains(20, day=DAY)
    assert [len(ch) for ch in chains] == [3], "13' and 19' chain three trips; 47' is over the limit"


# ── the page ──────────────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def test_the_drawer_draws_six_cards_for_six_trips(tmp_path, monkeypatch):
    """The heading says "6 trips"; the list under it used to hold ten cards."""
    import asyncio
    import re
    db_reader = _riri19_day(tmp_path, monkeypatch)
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    body = asyncio.run(main.trips_calendar_day(_Req(), 2026, 8, 14, merge=1, gap=90)).body.decode()

    cards = re.findall(r'data-trip-id="(\d+)"', body)
    assert len(cards) == 6, f"{len(cards)} cards for 6 trips: {cards}"
    assert len(set(cards)) == 6
    assert body.count("api/trips/merge-preview?a=") == 5, "one connector between each pair"
