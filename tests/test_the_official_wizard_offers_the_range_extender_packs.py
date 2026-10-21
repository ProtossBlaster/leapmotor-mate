"""The official wizard offers the range-extender packs (BetaTester #13).

Since 4.7.0 the server hands the wizard every pack, range-extenders included
(`battery_options_for_build`), but the page took them out again on every build but the BetaTester:
`if (!RESEARCH) options = options.filter(o => !o.reev)`, in the one-car selector and in the per-car
cards. So a C10 or a B10 with a range extender could not pick its pack in the official wizard; an
owner moving across from the beta got theirs back only because the restore carries the setting.

Measured in a browser, because the filter was the page's own script: the server's list was right.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

import battery_packs  # noqa: E402
from web_in_a_browser import chromium, seed_database, served  # noqa: E402

_REEV = {ct: [o["label"] for o in opts if o.get("reev")]
         for ct, opts in battery_packs.EU_BATTERY_MAP.items() if any(o.get("reev") for o in opts)}

# The packs each of the page's two pickers draws for a model: the one-car selector and a car's card.
_DRAWN = """car_type => {
  const drawn = html => { const d = document.createElement('div'); d.innerHTML = html;
                          return [...d.querySelectorAll('.bat-opt')].map(e => e.innerText.trim()); };
  return {one: drawn(buildBatterySelector(BATTERY_OPTIONS[car_type])),
          card: drawn(buildCarCards([{vin: 'LVIN0000000000002', car_type,
                                      battery_options: BATTERY_OPTIONS[car_type]}]))};
}"""


@pytest.fixture(scope="module")
def wizard(tmp_path_factory):
    """The wizard of the official build (the browser helper serves it with MATE_RESEARCH=0)."""
    db = tmp_path_factory.mktemp("wizard") / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001")
    with served(db.parent, db) as url:
        pw, browser = chromium(sync_api)
        page = browser.new_page()
        page.goto(url + "/setup")
        yield page
        browser.close()
        pw.stop()


def test_the_models_with_a_range_extender_are_the_ones_this_file_checks():
    assert set(_REEV) == {"C10", "B10"}


@pytest.mark.parametrize("car_type", sorted(_REEV))
def test_the_official_wizard_offers_the_range_extender_pack(wizard, car_type):
    shown = wizard.evaluate(_DRAWN, car_type)
    every = [o["label"] for o in battery_packs.EU_BATTERY_MAP[car_type]]
    assert shown["one"] == every
    assert shown["card"] == every
