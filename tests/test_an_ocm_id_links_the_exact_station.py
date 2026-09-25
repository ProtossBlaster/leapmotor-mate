"""An Open Charge Map identifier typed into ✏️ links that exact station (#301 @jcconca).

The 🔄 lookup searches around the charge's coordinates, so a charge typed in by hand — which has
none by construction — could only ever get a free-text label. But the owner often already HAS the
station's identity: the OCM page they found it on, its link, its number. That is not a search.
There is nothing to rank and nothing to guess, so there is nothing to get wrong.

Searching by NAME was measured and left out on purpose (see #301): a geocoded operator name lands
hundreds of metres to thousands of kilometres from the station, and on that wrong point the
coordinate lookup happily offers right-looking candidates. Only the exact half is here.

What the tests hold:
  - only a real identifier is recognised — a name that happens to contain a number is a name;
  - the POI fetched is the one asked for, never a neighbour OCM returns instead;
  - a failed fetch writes NOTHING (the id is not a label, saving it as one would bury it);
  - the station's position is not written onto the charge (it is not where the car was, and a
    charge without coordinates is how a hand-typed one is recognised).
"""
import asyncio
import json
import pathlib

import jinja2
import pytest

import db as D
import db_reader
import charger_locator as CL


# ── recognising an identifier ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "https://openchargemap.org/poi/details/280221",
    "https://www.openchargemap.org/poi/details/280221",
    "openchargemap.org/site/poi/details/280221",          # the old path people still copy
    "https://map.openchargemap.io/?id=1#  https://openchargemap.org/poi/details/280221",
    "OCM-280221",
    "ocm 280221",
    "OCM#280221",
    "Parque Comercial Sedaví (Zunder) OCM-280221",         # as already typed on the charge in #301
    "280221",
    "  #280221 ",
])
def test_an_identifier_is_recognised(text):
    assert CL.parse_ocm_id(text) == 280221


@pytest.mark.parametrize("text", [
    "", None, "Ionity Binasco", "Station 12", "A1 km 12", "Enel X 22 kW", "0", "OCM", "OCM-",
    "https://www.openstreetmap.org/node/280221",           # an OSM id is not an OCM id
])
def test_a_name_is_not_an_identifier(text):
    assert CL.parse_ocm_id(text) is None


# ── fetching it ──────────────────────────────────────────────────────────────

def _poi(poi_id, title="Parque Comercial Sedaví (Zunder)", lat=39.421351, lon=-0.376533):
    return {"ID": poi_id,
            "AddressInfo": {"Title": title, "Latitude": lat, "Longitude": lon,
                            "AddressLine1": "Av. Mediterrània", "Town": "Sedaví"},
            "Connections": [{"PowerKW": 150.0, "CurrentTypeID": 30}]}


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def ocm(monkeypatch):
    """Fake OCM: returns `body`, records the URL asked for."""
    seen = []
    state = {"body": [], "raise": None}

    def urlopen(req, timeout=0):
        seen.append(req.full_url)
        if state["raise"]:
            raise state["raise"]
        return _Resp(state["body"])

    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL.urllib.request, "urlopen", urlopen)
    return state, seen


def test_the_station_is_fetched_by_its_id(ocm):
    state, seen = ocm
    state["body"] = [_poi(280221)]

    st, reason = CL.ocm_station_by_id(280221)

    assert reason is None
    assert st["name"] == "Parque Comercial Sedaví (Zunder)"
    assert st["url"] == "https://openchargemap.org/poi/details/280221"
    assert "chargepointid=280221" in seen[0]
    assert "latitude" not in seen[0]                       # no search around anything


def test_a_different_poi_in_the_answer_is_not_taken(ocm):
    """Should OCM ever ignore the filter and answer with some other station, that station is not
    the one the owner pasted — linking it would be exactly the silent wrong answer #301 rejects."""
    state, _ = ocm
    state["body"] = [_poi(999, title="Somewhere else")]

    assert CL.ocm_station_by_id(280221) == (None, "not_found")


def test_an_unknown_id_is_not_found(ocm):
    state, _ = ocm
    state["body"] = []
    assert CL.ocm_station_by_id(280221) == (None, "not_found")


def test_an_unreachable_ocm_is_an_error_not_a_miss(ocm):
    state, _ = ocm
    state["raise"] = OSError("timed out")
    assert CL.ocm_station_by_id(280221) == (None, "error")


def test_without_a_key_nothing_is_asked(monkeypatch):
    """OCM's API refuses every request without a key (403, verified live) — say so up front."""
    monkeypatch.setattr(CL, "_ocm_key", lambda: "")
    monkeypatch.setattr(CL.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("no key, no request"))
    assert CL.ocm_station_by_id(280221) == (None, "missing_key")


def test_the_radius_search_still_reads_stations_the_same_way(ocm):
    """_ocm_poi was lifted out of _ocm_stations so both paths name a station identically."""
    state, _ = ocm
    state["body"] = [_poi(280221)]
    near = CL._ocm_stations(39.421351, -0.376533, 150)
    by_id, _ = CL.ocm_station_by_id(280221)
    assert near[0]["dist_m"] == 0
    assert {k: v for k, v in near[0].items() if k != "dist_m"} == by_id


# ── the ✏️ endpoint ──────────────────────────────────────────────────────────

class _Req:
    def __init__(self, name):
        self._name = name

    async def form(self):
        return {"name": self._name}


class _FakeTemplates:
    def __init__(self):
        self.rendered = []

    def TemplateResponse(self, request, name, ctx):   # noqa: N802 — mirrors Starlette's name
        self.rendered.append((name, ctx))
        return ctx


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    fake = _FakeTemplates()
    monkeypatch.setattr(main, "templates", fake)

    def add_charge(name=None, url=None, lat=None, lon=None):
        pdb._conn.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, latitude, longitude,"
            " location_type, location_name, location_url)"
            " VALUES (1,'2026-07-01T10:00:00+00:00','2026-07-01T11:00:00+00:00',?,?,'FAST',?,?)",
            (lat, lon, name, url))
        pdb._conn.commit()
        return db_reader._get().execute("SELECT MAX(id) AS id FROM charges").fetchone()["id"]

    calls = []

    def answer(station=None, reason=None):
        def fake(poi_id):
            calls.append(poi_id)
            return station, reason
        monkeypatch.setattr(CL, "ocm_station_by_id", fake)

    return main, fake, add_charge, answer, calls


_SEDAVI = {"name": "Parque Comercial Sedaví (Zunder)", "lat": 39.421351, "lon": -0.376533,
           "info": "DC · 150 kW", "address": None,
           "url": "https://openchargemap.org/poi/details/280221"}


def test_a_pasted_link_on_a_hand_typed_charge_links_the_station(env):
    """The case from #301: a charge typed in by hand, no coordinates, no 🔄."""
    main, fake, add_charge, answer, calls = env
    cid = add_charge(name="Parque Comercial Sedaví (Zunder) OCM-280221")
    answer(_SEDAVI)

    asyncio.run(main.set_manual_charge_location(
        _Req("https://openchargemap.org/poi/details/280221"), cid))

    assert calls == [280221]
    row = db_reader.get_charge_location(cid)
    assert row["location_name"] == "Parque Comercial Sedaví (Zunder)"
    assert row["location_url"] == "https://openchargemap.org/poi/details/280221"
    # the STATION's position is not where the car was — and no coordinates is what marks a
    # hand-typed charge, so writing them would quietly turn it into something else
    assert row["latitude"] is None and row["longitude"] is None


@pytest.mark.parametrize("reason", ["missing_key", "not_found", "error"])
def test_a_failed_fetch_writes_nothing(env, reason):
    main, fake, add_charge, answer, calls = env
    cid = add_charge(name="Old name", url="https://old/1")
    answer(None, reason)

    asyncio.run(main.set_manual_charge_location(_Req("OCM-280221"), cid))

    row = db_reader.get_charge_location(cid)
    assert (row["location_name"], row["location_url"]) == ("Old name", "https://old/1")
    assert fake.rendered[-1][1]["ocm_error"] == reason       # and the user is told which


def test_a_failed_fetch_on_an_unlabelled_charge_leaves_it_in_the_sweep(env):
    main, fake, add_charge, answer, calls = env
    cid = add_charge(lat=45.0, lon=9.0)
    answer(None, "error")

    asyncio.run(main.set_manual_charge_location(_Req("280221"), cid))

    assert db_reader.get_charge_location(cid)["location_name"] is None
    assert any(c["id"] == cid for c in db_reader.get_location_lookup_candidates())


def test_a_plain_name_is_still_just_a_name(env):
    main, fake, add_charge, answer, calls = env
    cid = add_charge()
    answer(_SEDAVI)

    asyncio.run(main.set_manual_charge_location(_Req("Colonnina del bar Mario 2"), cid))

    assert calls == []                                       # OCM never asked
    row = db_reader.get_charge_location(cid)
    assert (row["location_name"], row["location_url"]) == ("Colonnina del bar Mario 2", None)


# ── what the owner sees ──────────────────────────────────────────────────────

LOCALES = ("en", "it", "de", "fr", "es", "nl", "pl", "pt-PT")


@pytest.mark.parametrize("reason", ["missing_key", "not_found", "error"])
def test_the_failure_is_shown(reason):
    tpl = jinja2.Environment(
        loader=jinja2.FileSystemLoader(
            str(pathlib.Path(__file__).resolve().parent.parent / "web" / "templates")),
        autoescape=True).get_template("partials/charge_location.html")
    html = tpl.render(charge={"id": 7, "location_type": "FAST", "location_name": "Old name"},
                      t=lambda k, **kw: k, ocm_error=reason)
    assert f"charger_locator_ocm_{reason}" in html
    assert "Old name" in html


def test_every_locale_says_it():
    import i18n
    for lang in LOCALES:
        t = i18n.get_t(lang)
        for key in ("charger_locator_ocm_missing_key", "charger_locator_ocm_not_found",
                    "charger_locator_ocm_error"):
            assert t(key) != key, f"{lang} is missing {key}"
        assert "OCM" in t("charger_locator_manual_ph"), f"{lang} placeholder doesn't mention OCM"
