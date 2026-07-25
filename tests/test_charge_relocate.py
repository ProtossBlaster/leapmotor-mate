"""The 📍 manual recalculation button, on a single charge.

Its whole reason to exist is that the unattended sweep has to guess: it takes the nearest name
and moves on. The button lets a human look instead — and, when the sources disagree, decide.

Which makes one behaviour non-negotiable: it must never end up DESTROYING the label it was asked
to refresh. An empty answer from the lookup is not proof that the station is gone; it is equally
what you get when the Open Charge Map key was removed, when a node was deleted from OSM, or when
the charge sits a few metres further out than the search radius. And an empty string is not a
blank in this column — it is the sweep's "resolved: nothing here" sentinel, so a wiped name is
never looked up again by itself.
"""
import asyncio

import pytest

import db as D
import db_reader


class _Req:
    """The endpoint only ever passes this to the template renderer, which is faked below."""


class _FakeTemplates:
    """Captures what the endpoint decided to render, without needing Starlette."""

    def __init__(self):
        self.rendered = []

    def TemplateResponse(self, request, name, ctx):   # noqa: N802 — mirrors Starlette's name
        self.rendered.append((name, ctx))
        return ctx


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import charger_locator
    import main

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    fake = _FakeTemplates()
    monkeypatch.setattr(main, "templates", fake)

    def add_charge(name=None, url=None, lat=45.0, lon=9.0, location_type="FAST"):
        pdb._conn.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, latitude, longitude,"
            " location_type, location_name, location_url)"
            " VALUES (1,'2026-07-01T10:00:00+00:00','2026-07-01T11:00:00+00:00',?,?,?,?,?)",
            (lat, lon, location_type, name, url))
        pdb._conn.commit()
        return db_reader._get().execute("SELECT MAX(id) AS id FROM charges").fetchone()["id"]

    def answer(options, ok=True):
        monkeypatch.setattr(charger_locator, "find_station_candidates", lambda la, lo: (options, ok))

    return main, fake, add_charge, answer


def test_an_empty_result_does_not_wipe_the_saved_name(env):
    """The bug this test exists for: the lookup answers, finds nothing, and the label that was
    already there is overwritten with the "nothing here" sentinel — gone, and never re-resolved."""
    main, fake, add_charge, answer = env
    cid = add_charge(name="Ionity Binasco", url="https://openstreetmap.org/node/1")
    answer([])

    asyncio.run(main.relocate_charge(_Req(), cid))

    row = db_reader.get_charge_location(cid)
    assert row["location_name"] == "Ionity Binasco"
    assert row["location_url"] == "https://openstreetmap.org/node/1"
    assert fake.rendered[-1][1]["not_found"] is True     # and the user is told, not left guessing


def test_an_empty_result_on_an_unlabelled_charge_writes_nothing(env):
    """No name to protect here — but writing the sentinel would take the charge out of the
    ongoing sweep's queue for good. Doing nothing leaves it exactly as if the button had never
    been pressed, so the next sweep still tries."""
    main, fake, add_charge, answer = env
    cid = add_charge()
    answer([])

    asyncio.run(main.relocate_charge(_Req(), cid))

    assert db_reader.get_charge_location(cid)["location_name"] is None
    assert db_reader.get_location_lookup_candidates(10)   # still in the sweep's queue


def test_a_single_match_is_saved_with_its_link(env):
    main, fake, add_charge, answer = env
    cid = add_charge()
    answer([{"name": "Ionity Binasco", "url": "https://openchargemap.org/poi/details/7", "dist_m": 20}])

    asyncio.run(main.relocate_charge(_Req(), cid))

    row = db_reader.get_charge_location(cid)
    assert row["location_name"] == "Ionity Binasco"
    assert row["location_url"] == "https://openchargemap.org/poi/details/7"


def test_a_single_match_replaces_a_previous_name(env):
    """Not-wiping must not turn into never-updating: a real answer still wins over the old one."""
    main, fake, add_charge, answer = env
    cid = add_charge(name="Old name", url="https://old/1")
    answer([{"name": "Free To X", "url": "https://openchargemap.org/poi/details/9", "dist_m": 30}])

    asyncio.run(main.relocate_charge(_Req(), cid))

    assert db_reader.get_charge_location(cid)["location_name"] == "Free To X"


def test_several_matches_ask_instead_of_writing(env):
    """Ambiguity is the button's whole point — nothing is saved until the user picks."""
    main, fake, add_charge, answer = env
    cid = add_charge(name="Old name")
    answer([{"name": "Free To X", "url": None, "dist_m": 30},
            {"name": "Area di Servizio - Flaminia Ovest", "url": None, "dist_m": 72}])

    asyncio.run(main.relocate_charge(_Req(), cid))

    assert db_reader.get_charge_location(cid)["location_name"] == "Old name"   # untouched
    assert fake.rendered[-1][0].endswith("charge_location_choices.html")
    assert len(fake.rendered[-1][1]["options"]) == 2


def test_a_transient_failure_changes_nothing(env):
    """ok=False means the sources were unreachable, not that the station is gone."""
    main, fake, add_charge, answer = env
    cid = add_charge(name="Ionity Binasco")
    answer([], ok=False)

    asyncio.run(main.relocate_charge(_Req(), cid))

    assert db_reader.get_charge_location(cid)["location_name"] == "Ionity Binasco"
    assert fake.rendered[-1][1]["error"] is True


def test_confirming_a_pick_saves_that_one(env):
    main, fake, add_charge, answer = env
    cid = add_charge()

    class Form(_Req):
        async def form(self):
            return {"name": "Area di Servizio - Flaminia Ovest", "url": ""}

    asyncio.run(main.confirm_charge_location(Form(), cid))

    row = db_reader.get_charge_location(cid)
    assert row["location_name"] == "Area di Servizio - Flaminia Ovest"
    assert row["location_url"] is None      # empty form field is a missing link, not an empty one


def test_the_two_empty_answers_read_differently(env):
    """A charge that keeps its name and one that never had one are told different things — the
    first needs to know the button did something and decided to leave it alone."""
    import i18n
    t = i18n.get_t("it")
    assert t("charger_locator_relocate_kept") != t("charger_locator_relocate_none")
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT"):
        tl = i18n.get_t(lang)
        for key in ("charger_locator_relocate_kept", "charger_locator_relocate_none"):
            assert tl(key) != key, f"{lang} is missing {key}"
