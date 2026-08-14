"""AVERAGE DURATION on the Charges page must average charges, not blackouts.

A reconstructed charge is not a session Mate watched: it is an SoC jump found after the fact, when
the car had been unreachable for hours. Its `duration_min` is the length of **that silence**, not
of any charging — nothing was measured while it lasted. Averaged in with the observed sessions it
does not shift the figure, it replaces it.

Measured on the real database while this was found: 27 observed charges average **193 min**; adding
the single reconstructed #55 (1091.5 min) makes the page read **3.8 h** — **+18% from one row**.

It is [[feedback-two-numbers-one-word]]: two quantities under one word. So the average drops them,
and the card SAYS how many it dropped — silently averaging fewer rows than the "sessions" tile
counts is how the next reader gets to distrust both numbers.

⚠️ Merged groups are deliberately left in. Their duration is end-minus-start, so the pause inside a
plug-in the car reported in pieces counts too — but that pause is time the cable really was in the
car, which is what the page's own "18:00 → 18:38" window means. A blackout is not.
"""
import pytest


def _install(tmp_path, monkeypatch, charges):
    """charges: (duration_min, reconstructed) — everything else is what a closed charge needs."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    for i, (dur, recon) in enumerate(charges, start=1):
        c.execute(
            "INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh,"
            " duration_min, start_soc, end_soc, reconstructed)"
            " VALUES (?,1,?,?,10.0,?,40.0,60.0,?)",
            (i, f"2026-08-{i:02d}T20:00:00+00:00", f"2026-08-{i:02d}T23:00:00+00:00", dur, recon))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader.get_charge_stats()


def test_a_blackout_never_enters_the_average(tmp_path, monkeypatch):
    """The shape of the real case: three ordinary evenings and one 18-hour silence."""
    s = _install(tmp_path, monkeypatch, [(180, 0), (180, 0), (180, 0), (1091.5, 1)])
    assert s["avg_duration_h"] == 3.0


def test_without_the_guard_that_one_row_would_have_moved_it(tmp_path, monkeypatch):
    """Proves the fixture actually reproduces the defect it is guarding against: averaged over all
    four rows the same data reads 6.8 h — more than twice the truth. A test that cannot fail on the
    old code guards nothing."""
    s = _install(tmp_path, monkeypatch, [(180, 0), (180, 0), (180, 0), (1091.5, 1)])
    assert round((180 + 180 + 180 + 1091.5) / 4 / 60, 1) == 6.8
    assert s["avg_duration_h"] != 6.8


def test_the_card_says_how_many_it_left_out(tmp_path, monkeypatch):
    s = _install(tmp_path, monkeypatch, [(180, 0), (180, 0), (1091.5, 1), (900, 1)])
    assert s["duration_excluded"] == 2
    assert s["session_count"] == 4, "the sessions tile still counts every charge"


def test_nothing_to_declare_when_every_charge_was_watched(tmp_path, monkeypatch):
    s = _install(tmp_path, monkeypatch, [(180, 0), (240, 0)])
    assert s["duration_excluded"] == 0
    assert s["avg_duration_h"] == 3.5


def test_an_install_of_nothing_but_blackouts_has_no_average(tmp_path, monkeypatch):
    """Not zero: unknown. A 0.0 h average would read as "charges here are instantaneous"."""
    s = _install(tmp_path, monkeypatch, [(1091.5, 1), (900, 1)])
    assert s["avg_duration_h"] is None
    assert s["duration_excluded"] == 2


# ── the page ──────────────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _charges_page(tmp_path, monkeypatch, charges):
    import asyncio

    import db_reader
    _install(tmp_path, monkeypatch, charges)
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    return asyncio.run(main.charges_page(_Req())).body.decode()


def test_the_page_declares_the_charges_left_out(tmp_path, monkeypatch):
    import json
    import pathlib
    body = _charges_page(tmp_path, monkeypatch, [(180, 0), (180, 0), (1091.5, 1)])
    tr = json.loads((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales" /
                     "en.json").read_text())["translations"]
    assert tr["avg_duration_partial"].format(n=1) in body, "the page averages fewer rows in silence"


def test_the_page_says_nothing_when_there_is_nothing_to_say(tmp_path, monkeypatch):
    body = _charges_page(tmp_path, monkeypatch, [(180, 0), (240, 0)])
    assert "reconstructed" not in body.lower()
