"""The generator's distance, measured where the defect actually lives: in the rendered page.

@gm27271, beta #31 — *«let's display this distance in crappy comment after loads of text like
today»*. Everything about that sentence is true of the HTML, and none of it can be read off the
HTML: the figure inherited its type from the paragraph it was glued to, so only the browser knows
what size it came out at. On the released build, on a generator trip:

    the paragraph before it   217 characters, 41 words
    the figure's own type     10px, rgb(100,116,139)
    the same type as          "v3.14.23", the build number in the sidebar corner

So this test seeds one trip shaped like his (176 km, the generator running for 25 of them), serves
the page, and asks the browser two questions: how big is that number, and what colour.

Cost, and the limit, of every browser test here: fastapi + uvicorn + pytest-playwright + a
Chromium, none of them in CI's minimal env, so this file SKIPS there and guards this laptop. The
source-level half — that the figure left the note, is printed once, and carries its floor — is in
test_the_generator_distance_has_a_line_of_its_own.py and runs everywhere.
"""
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="needs pytest-playwright + `playwright install chromium`",
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOOTNOTE_GREY = "rgb(100, 116, 139)"      # slate-500, what the sidebar prints the version in
VIN = "LREEVC10TEST00001"
T0 = datetime(2026, 8, 20, 8, 0, 0, tzinfo=timezone.utc)
TANK_L = 47.5
DIST_KM, SAMPLES = 176.0, 240
GEN_FROM, GEN_TO, BURN_L = 100, 134, 2.0   # 34 of the 239 intervals → ~25 km


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(db_path: pathlib.Path) -> None:
    """One range-extender trip with a generator stretch inside it.

    `engine_km` is not a stored column: `_reev_engine_on` walks the positions log and counts the
    intervals where the odometer rises AND the fuel falls in the same row. A trip with no positions
    yields None and the line under test never renders — hence a sample every 30 s, with the fuel
    moving only across the middle stretch. The litres come from `fuel_start_l`/`fuel_end_l` (the
    car's own counter), the percentages from the same litres over the C10's 47.5 L tank.
    """
    sys.path.insert(0, str(ROOT / "poller"))
    import schema
    con = sqlite3.connect(db_path)
    schema.ensure_schema(con)
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'C10')", (VIN,))
    for k, v in (("setup_complete", "1"), ("is_reev", "1"), (f"is_reev_{VIN.lower()}", "1"),
                 ("research_consent", "1"), ("price_home_kwh", "0.24"), ("currency", "EUR")):
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))
    # A charge before it, so the trip has a paid stock to draw on and the block renders whole.
    con.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, cost, charge_type, location_type) VALUES (1,?,?,20,60,20.0,4.80,"
        "'AC','HOME')",
        ((T0 - timedelta(hours=12)).isoformat(), (T0 - timedelta(hours=10)).isoformat()))

    step, odo0, fuel_l = DIST_KM / (SAMPLES - 1), 12000.0, 30.0
    rows = []
    for i in range(SAMPLES):
        if GEN_FROM < i <= GEN_TO:
            fuel_l -= BURN_L / (GEN_TO - GEN_FROM)
        rows.append(((T0 + timedelta(seconds=30 * i)).isoformat(), odo0 + step * i,
                     round(fuel_l, 4), round(fuel_l / TANK_L * 100, 2),
                     round(78.0 - 44.0 * i / (SAMPLES - 1), 1)))
    con.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, odometer_km, fuel_liters,"
        " fuel_level_pct, soc, latitude, longitude) VALUES (1,?,?,?,?,?,45.46,9.19)", rows)
    con.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " start_odometer_km, end_odometer_km, duration_min, ec_kwh, ec_driving, ec_ac, ec_other,"
        " ec_stable, ec_tried, fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l)"
        " VALUES (1,1,?,?,?,78,34,?,?,120,28.5,24.1,2.6,1.8,1,1,?,?,30.0,?)",
        (T0.isoformat(), (T0 + timedelta(minutes=120)).isoformat(), DIST_KM,
         odo0, odo0 + DIST_KM, round(30.0 / TANK_L * 100, 2),
         round((30.0 - BURN_L) / TANK_L * 100, 2), 30.0 - BURN_L))
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("reev-legible")
    db = tmp / "leapmotor_mate.db"
    _seed(db)
    port = _free_port()
    env = {**os.environ, "DB_PATH": str(db), "WEB_PORT": str(port),
           "PYTHONPATH": str(ROOT / "web"), "MATE_RESEARCH": "1"}
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        env.pop(leak, None)
    log = (tmp / "web.log").open("w")
    proc = subprocess.Popen([sys.executable, str(ROOT / "web" / "main.py")], env=env,
                            stdout=log, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            break
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(0.2)
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1200, "height": 1400})
        pg.goto(f"{url}/trips/1", wait_until="networkidle")
        pg.wait_for_timeout(800)
        yield pg
        browser.close()
    proc.terminate()


def _figure(page):
    """{text, size, color, words} of the tightest element that prints the generator's distance.

    ⚠️ Asking for a CSS selector would assert the markup, which is exactly what this change is free
    to rewrite. So it asks for the SMALLEST element on the page whose text carries both ⛽ and a
    distance — on the released build that is the getEC paragraph with the figure glued to its end,
    and after the change it is the figure's own line. Same question, either way.

    🔴 The first version looked for a leaf reading "25 km" and found "12000 km", the start
    odometer: today no element holds that figure alone, so the search fell through to an unrelated
    number and three assertions passed while measuring nothing. Hence both the ⛽ and the guard
    test below."""
    return page.evaluate("""() => {
        let best = null;
        document.querySelectorAll('*').forEach(el => {
            const t = (el.textContent || '').trim();
            if (!t.includes('\\u26FD')) return;                 // ⛽
            if (/\\/\\s*100\\s*km/.test(t)) return;              // "1.1 L/100km" is a rate
            if (!/(^|[^\\/\\d])\\d+([.,]\\d+)?\\s*km\\b/.test(t)) return;
            if (best && t.length >= best.text.length) return;
            const s = getComputedStyle(el);
            best = {text: t, size: parseFloat(s.fontSize), color: s.color,
                    words: t.split(/\\s+/).length};
        });
        return best;
    }""")


def test_the_seed_really_produced_a_generator_trip(page):
    """🔴 First, that the thing under test is on the page at all. Without this, a seed that failed
    to yield an `engine_km` would make every assertion below vacuously true — the shape of a test
    that measures nothing and reports success."""
    assert "25" in page.inner_text("body"), "the seed produced no generator distance to look at"
    fig = _figure(page)
    assert fig and "25" in fig["text"], f"no generator distance found on the page: {fig}"


def test_the_distance_is_not_printed_in_footnote_type(page):
    fig = _figure(page)
    assert fig["size"] > 10, (
        f"the generator's distance still renders at {fig['size']}px — the size the sidebar "
        f"prints the build number in")


def test_the_distance_is_not_printed_in_the_dimmest_grey(page):
    fig = _figure(page)
    assert fig["color"] != FOOTNOTE_GREY, (
        f"the generator's distance is still {fig['color']} — slate-500, the footnote colour")


def test_it_no_longer_hangs_off_the_end_of_a_paragraph(page):
    """The complaint in one measurement: how much prose the reader crosses before the number.

    41 words on the released build, because the figure was inside the getEC note. Its own line
    means its own element, so whatever text shares that element is a label, not an essay."""
    words = _figure(page)["words"]
    assert words <= 12, f"the distance still sits at the end of {words} words of prose"
