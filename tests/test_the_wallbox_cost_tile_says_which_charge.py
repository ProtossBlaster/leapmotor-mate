"""The cost on the Wallbox card is the last HOME charge, and the label has to say so (#248).

@Ng-EY, 14/08/2026, on a card where every other tile was a dash: *"I haven't had a charging session
since I connected to the Wall Connector but there is some costing data showing up."* Then, once it
was explained: *"my last charge was exactly that cost so it aligned with what you explained"*.

The number was never the session's. `latest_home_charge_cost()` returns the most recent charge
marked HOME, with no time limit — and a charge only gets a cost when it is CLOSED and typed
(`update_charge_type`; the poller never prices one, it only rescales an existing figure). So the
tile cannot show the session in progress even while the car is plugged in and charging: at that
moment the current charge has no cost yet, and what is printed is the previous one.

That is why the fix is the label, not a guard. Hiding the tile while the car is unplugged would
have left the same wrong word on the same wrong number during the session itself. Under "last home
charge" the figure is true in both states, and nothing is hidden from the owner.

⚠️ In Mate "Home" means *wallbox or domestic socket* (`home_desc`), so the label must not promise
the wallbox either.

The locale half runs anywhere; the route half skips without fastapi.
"""
import json
import pathlib

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
LOCALES = WEB / "locales"

# Each language's own word for "home", taken from the badge it already shows on a charge
# (`report_home`) — the label has to speak the app's vocabulary, not a fresh translation.
HOME_WORD = {
    "en": "home", "it": "casa", "fr": "domicile", "de": "zuhause",
    "pl": "domu", "pt-PT": "casa", "nl": "thuis", "es": "casa",
}
KEY = "wb_stat_last_home_charge"


def _translations(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text())["translations"]


@pytest.mark.parametrize("lang", sorted(HOME_WORD))
def test_the_label_names_the_home_charge(lang):
    t = _translations(lang)
    assert KEY in t, f"{lang}.json has no {KEY}"
    assert HOME_WORD[lang] in t[KEY].lower(), f"{lang}.{KEY} = {t[KEY]!r}"


@pytest.mark.parametrize("lang", sorted(HOME_WORD))
def test_the_old_session_label_is_gone(lang):
    """`wb_stat_cost` said "session" in six languages and "charging costs" in the other two. Left
    in place it is a translated lie waiting for the next person who needs a cost label."""
    assert "wb_stat_cost" not in _translations(lang)


# ── the page ──────────────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _render(tmp_path, monkeypatch, *, plugged, home_cost):
    import asyncio

    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
              " odometer_km, plug_connected) VALUES (1,'2026-08-14T06:00:00+00:00',55,0,0,1263,?)",
              (1 if plugged else 0,))
    if home_cost is not None:
        c.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, location_type, cost)"
                  " VALUES (1,'2026-08-10T20:00:00+00:00','2026-08-10T23:00:00+00:00','HOME',?)",
                  (home_cost,))
    pdb._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(main.ha_client, "get_live", lambda: {
        "power_kw": None, "status": None, "energy_kwh": None, "speed": None,
        "speed_unit": "", "max_power": None, "max_power_unit": "", "charging": False})
    return asyncio.run(main.wallbox_live(_Req())).body.decode()


def test_the_tile_carries_the_new_label(tmp_path, monkeypatch):
    body = _render(tmp_path, monkeypatch, plugged=False, home_cost=17.73)
    assert _translations("en")[KEY] in body, body[:600]


def test_the_figure_stays_visible_on_a_car_that_is_not_plugged_in(tmp_path, monkeypatch):
    """Ng-EY's exact screen. The number is his last home charge and it is his to see — the defect
    was the word above it, so the fix must not make the figure disappear."""
    assert "17.73" in _render(tmp_path, monkeypatch, plugged=False, home_cost=17.73)


def test_no_home_charge_yet_still_prints_a_dash(tmp_path, monkeypatch):
    body = _render(tmp_path, monkeypatch, plugged=False, home_cost=None)
    assert "17.73" not in body and "—" in body
