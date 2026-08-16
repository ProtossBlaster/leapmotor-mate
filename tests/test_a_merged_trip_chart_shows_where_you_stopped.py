"""On a merged trip's chart, the pause must be marked as a pause (#159, @pdifeo).

He posted a chart with a break in it and asked if it was normal. We answered: the car losing touch
with the cloud. He corrected us the next morning — *"What you see is a joint trip. From minute 14 to
minute 21, there's a stop. It might be helpful to indicate this on the graph."* — and we answered
about signal gaps and phone hotspots again. He restated it in Italian an hour later, *"Dal min 14 al
21 mi sono fermato. Poi ho unito i viaggi."*, and the thread went quiet for 22 days.

He was right and we sent him hunting a network problem for a stop he had made himself.

The chart draws the points of every segment in one row (`trip_id IN (…) ORDER BY recorded_at`), so
the pause between two joined pieces is a blank stretch **identical** to a signal dropout — and the
same chart already leaves blanks for real dropouts and for stretches dropped as cloud-cached. Three
different things, one appearance.

The boundaries are known: each piece keeps its own start and end (a merge writes only the marker),
so the pause is simply the hole between them. Marked, it says "you stopped here"; unmarked, it looks
like the car went silent.
"""
import pytest

pytest.importorskip("fastapi", reason="the chart lives in a page template")


def _install(tmp_path, monkeypatch, *, merged):
    """His shape: 0→14 min, a 7-minute stop, then 21→40 min."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
              " start_soc, end_soc) VALUES (1,1,'2026-07-24T08:00:00+00:00',"
              "'2026-07-24T08:14:00+00:00',9.0,14,80.0,76.0)")
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
              " start_soc, end_soc, merged_into_id) VALUES (2,1,'2026-07-24T08:21:00+00:00',"
              "'2026-07-24T08:40:00+00:00',12.0,19,76.0,70.0,?)", (1 if merged else None,))
    for tid, h0, m0, n in ((1, 8, 0, 14), (2, 8, 21, 19)):
        for k in range(0, n, 2):
            c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude,"
                      " speed_kmh, soc) VALUES (?,?,45.0,9.0,50,?)",
                      (tid, f"2026-07-24T{h0:02d}:{m0 + k:02d}:00+00:00", 80 - k * 0.2))
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','UTC')")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def test_the_pause_between_two_joined_pieces_is_reported(tmp_path, monkeypatch):
    """His minute 14 to minute 21."""
    d = _install(tmp_path, monkeypatch, merged=True)
    pauses = d.get_trip_detail(1)["merge_pauses"]
    assert len(pauses) == 1
    assert pauses[0]["minutes"] == 7
    assert pauses[0]["from"].endswith("08:14:00+00:00") or "08:14" in pauses[0]["from"]
    assert "08:21" in pauses[0]["to"]


def test_an_ordinary_trip_has_none(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, merged=False)
    assert d.get_trip_detail(1)["merge_pauses"] == []


def test_opening_the_child_shows_the_same_pause(tmp_path, monkeypatch):
    """The page resolves a child to its group, so the chart is the group's — and so is the band."""
    d = _install(tmp_path, monkeypatch, merged=True)
    assert len(d.get_trip_detail(2)["merge_pauses"]) == 1


def test_the_chart_draws_a_band_for_each_pause():
    """The wiring: the template must turn those pauses into an annotation on the axis, and label it
    with the duration — a shaded stretch with no explanation is just another mystery."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
           / "trip_detail.html").read_text()
    assert "merge_pauses" in src, "the chart never receives the pauses"
    assert "annotations" in src, "nothing is drawn on the axis"
    block = src[src.index("merge_pauses"):][:1400]
    assert "minutes" in block, "the band does not say how long the stop was"
