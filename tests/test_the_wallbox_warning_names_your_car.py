"""The Wallbox warning must name the car the owner actually has (#248, @Ng-EY).

14/08/2026: *"I'm using a C10 but it showed as B10 not connected. Some issue with that
notification?"*. His car was read correctly everywhere else — the model was written INSIDE the
sentence, in all eight languages, from the days when Mate only covered the B10:

    "wb_b10_unplugged": "B10 not connected — session data may be from another vehicle"

So every C10, T03, B05 and REEV owner reads the name of a car that is not theirs, on the one line
whose whole job is to say WHICH vehicle the numbers below might belong to. A warning about identity
that gets the identity wrong is worse than no warning: it invites the reader to dismiss it.

The car's model is already known — `vehicles.car_type`, the same value Settings prints in the
diagnostics line. Where it is not known yet (a fresh install, before the poller has upserted the
vehicle) the sentence stays generic rather than printing a blank, a `None` or a raw `{car}`.

The locale half runs anywhere; the route half skips without fastapi.
"""
import json
import pathlib

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
LOCALES = WEB / "locales"
LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es")

NAMED_KEY = "wb_car_unplugged"            # carries {car} — the model goes here
GENERIC_KEY = "wb_car_unplugged_unknown"  # no subject to name yet


def _translations(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text())["translations"]


# ── the strings themselves ────────────────────────────────────────────────────
@pytest.mark.parametrize("lang", LANGS)
def test_no_language_writes_a_model_name_into_that_warning(lang):
    """The defect, at its source. Checked per language because the sentence was translated eight
    times WITH the model baked in, so fixing English alone leaves seven copies of it."""
    t = _translations(lang)
    for key in (NAMED_KEY, GENERIC_KEY):
        assert key in t, f"{lang}.json has no {key}"
        for model in ("B10", "C10", "T03", "B05"):
            assert model not in t[key], f"{lang}.{key} still names a model: {t[key]!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_the_named_warning_has_a_place_to_put_the_car(lang):
    assert "{car}" in _translations(lang)[NAMED_KEY]


@pytest.mark.parametrize("lang", LANGS)
def test_the_generic_warning_never_asks_for_a_car(lang):
    """It is rendered without arguments — a leftover placeholder would print as literal braces."""
    assert "{" not in _translations(lang)[GENERIC_KEY]


@pytest.mark.parametrize("lang", LANGS)
def test_the_old_key_is_gone_everywhere(lang):
    """Left behind, it is a translated sentence saying "B10" that nothing renders — and the next
    person to need this warning would find it first."""
    assert "wb_b10_unplugged" not in _translations(lang)


# ── the page ──────────────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _render(tmp_path, monkeypatch, *, car_type, plugged=False, power_kw=None):
    """The real partial, out of the real route: the model has to survive the trip from the DB to
    the sentence. A template rendered by hand would pass on a route that never passes the car."""
    import asyncio

    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    if car_type is not None:
        c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001',?)",
                  (car_type,))
    c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
              " odometer_km, plug_connected) VALUES (1,'2026-08-14T06:00:00+00:00',55,0,0,1263,?)",
              (1 if plugged else 0,))
    pdb._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    # No Home Assistant in the tests: the live tiles are empty, which is also Ng-EY's screen.
    monkeypatch.setattr(main.ha_client, "get_live", lambda: {
        "power_kw": power_kw, "status": None, "energy_kwh": None, "speed": None,
        "speed_unit": "", "max_power": None, "max_power_unit": "", "charging": False})
    return asyncio.run(main.wallbox_live(_Req())).body.decode()


def test_the_warning_names_the_car_you_have(tmp_path, monkeypatch):
    """His case: a C10 that is not plugged in."""
    body = _render(tmp_path, monkeypatch, car_type="C10")
    assert "C10 " in body, body[:400]
    assert "B10" not in body, "the warning still names the B10"


def test_a_b10_owner_still_reads_b10(tmp_path, monkeypatch):
    """The old sentence was right for exactly one model; it must stay right for it."""
    assert "B10 " in _render(tmp_path, monkeypatch, car_type="B10")


def test_before_the_car_is_known_the_warning_still_warns(tmp_path, monkeypatch):
    """A fresh install can open the Wallbox page before the poller has seen the car. The line must
    not print a blank subject, the word None, or an unrendered placeholder."""
    body = _render(tmp_path, monkeypatch, car_type=None)
    assert "⚠" in body, "the warning disappeared when the model was unknown"
    assert "None" not in body and "{car}" not in body, body[:400]


def test_a_plugged_in_car_gets_no_warning_at_all(tmp_path, monkeypatch):
    """The warning is about the session data belonging to someone else; plugged in, it does not."""
    assert "⚠" not in _render(tmp_path, monkeypatch, car_type="C10", plugged=True)


def test_a_plugged_in_car_still_shows_its_live_reading(tmp_path, monkeypatch):
    """The same flag that hides the warning is what UNLOCKS the six tiles: rename it in the route
    and miss one copy in the template, and every tile silently falls to a dash — a page that reads
    exactly like the wallbox never reporting. Checked here because "no warning" alone passes
    happily on a page where nothing is left."""
    body = _render(tmp_path, monkeypatch, car_type="C10", plugged=True, power_kw=7.4)
    assert "7.4" in body, "the power tile went dark on a car that IS plugged in"
