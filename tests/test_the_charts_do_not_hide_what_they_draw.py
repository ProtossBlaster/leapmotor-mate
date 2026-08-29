"""Two charts, measured in a real browser — is what they draw actually visible?

Both defects arrived the same day, from two different owners, and neither is about a wrong number:
the figures were right and the pixels were not.

  #268 (@pdifeo) — Battery health, "Estimated capacity per charge": ApexCharts pins a y-annotation
  label to the RIGHT edge of the plot, which is exactly where the newest charges sit. The
  "Nominal 28.4 kWh" box sat on top of the last four dots — the ones you open the chart to read.

  #269 (@adoewa) — Charges, the AC/DC ring: ApexCharts styles the ring's *label* and the percentage
  on the segment, but leaves the number in the middle on its own default fill, a near-black that
  disappears on our dark card. His screenshot has everything legible except the count.

So neither is checked by looking at the HTML: what the templates emit was correct in both cases.
These two tests read the drawing itself — the label's box against the dots' boxes, and the fill the
browser actually resolved — which is the only place the defect exists.

Costs, and the limit, of every browser test here: fastapi + uvicorn + pytest-playwright + a
Chromium, none of them in CI's minimal env, so this file SKIPS there and guards this laptop.
"""
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time
import types
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
NOMINAL_KWH = 69.9


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(db_path: pathlib.Path) -> None:
    """Eight AC charges, each with the telemetry battery health integrates.

    `get_battery_health` does not read `energy_added_kwh`: it integrates |V·I|dt over the logged
    samples and divides by the SoC that rose across them, so a charge with no `positions` rows is
    skipped entirely and the chart comes back empty. Hence 30 samples per charge, five minutes
    apart, at 350 V · 33.4 A ≈ 11.7 kW — 28.25 kWh over 40 SoC points, an estimate of 70.6 kWh
    against a 69.9 nominal.

    That 0.7 kWh is the whole point, and the first version of this seed got it backwards. At 32 A
    the estimate came out at 67.7 — BELOW the line — and the dots sat under an annotation label
    that hangs above it, so the test passed on the released template and proved nothing. @pdifeo's
    car reads 29 against a 28.4 nominal: the dots are just ABOVE the line, which is where the label
    is, and that is the only arrangement in which the defect exists.

    20 → 60 % and never near the top: above 95 % (`_SOH_TOP_CUTOFF_SOC`) the integration stops, and
    below 15 % the point is excluded as a near-empty start. `charge_type` is written 'AC'
    explicitly — left to the power heuristic, 11.7 kW is over the 11 kW AC ceiling and would
    have turned every one of these into a DC session.
    """
    import schema  # poller/ is on sys.path via tests/conftest.py

    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, ?)",
                     ("TESTVIN0000000001", "C10"))
        # Pinned, so the annotation line sits at a known height instead of following whatever the
        # model table says today.
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                     ("battery_capacity_nominal_kwh", str(NOMINAL_KWH)))
        conn.execute("INSERT INTO settings (key, value) VALUES ('setup_complete', '1')")

        for c in range(8):
            start = now - timedelta(days=(8 - c) * 3, hours=3)
            end = start + timedelta(minutes=145)
            conn.execute(
                "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc, "
                "energy_added_kwh, duration_min, charge_type, max_power_kw, cost) "
                "VALUES (1, ?, ?, 20.0, 60.0, 28.0, 145, 'AC', 11.0, 5.0)",
                (start.isoformat(), end.isoformat()))
            for i in range(30):
                t = start + timedelta(minutes=5 * i)
                conn.execute(
                    "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, "
                    "charge_voltage_v, charge_current_a, battery_min_temp, odometer_km) "
                    "VALUES (1, ?, ?, 1, 350.0, 33.4, 22.0, ?)",
                    (t.isoformat(), 20.0 + i * (40.0 / 29), 10000.0 + c * 200))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def mate_url(tmp_path_factory):
    """A real Mate, serving a database of our own, on a port of its own."""
    data = tmp_path_factory.mktemp("mate-charts-browser")
    db = data / "leapmotor_mate.db"
    _seed(db)

    port = _free_port()
    env = {**os.environ,
           "DB_PATH": str(db),
           "WEB_PORT": str(port),
           "PYTHONPATH": str(ROOT / "web"),
           "MATE_RESEARCH": "0"}
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        env.pop(leak, None)

    log = data / "web.log"
    proc = subprocess.Popen([sys.executable, str(ROOT / "web" / "main.py")], env=env,
                            stdout=log.open("w"), stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"the web process died before it served anything:\n{log.read_text()}")
            try:
                urllib.request.urlopen(url, timeout=1).read()
                break
            except urllib.error.HTTPError:
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.2)
        else:
            pytest.fail(f"the web process never answered:\n{log.read_text()}")
        yield types.SimpleNamespace(url=url, log=log)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _open(server, path: str, shot: str | None = None):
    """Load a page, let ApexCharts finish, hand back (page, browser, playwright) still open."""
    pw = sync_api.sync_playwright().start()
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 950})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    response = page.goto(f"{server.url}{path}", wait_until="networkidle")
    page.wait_for_timeout(1500)
    if shot and os.environ.get("MATE_SHOT_DIR"):
        page.screenshot(path=str(pathlib.Path(os.environ["MATE_SHOT_DIR"]) / shot),
                        full_page=True)
    return page, browser, pw, (response.status if response else None), errors


# ── #268 — the "Nominal" label must not sit on the dots ──────────────────────────────────────────
def test_the_nominal_label_does_not_cover_the_newest_dots(mate_url):
    page, browser, pw, status, errors = _open(mate_url, "/battery", "battery-nominal.png")
    try:
        assert status == 200, f"Battery health did not render: HTTP {status}"
        # The measurement: the label's own box against every plotted marker's box. Rectangles, not
        # a look — "the label is roughly over there" is exactly the judgement that shipped #268.
        overlap = page.evaluate(
            """() => {
                 const label = document.querySelector('.apexcharts-yaxis-annotations rect')
                            || document.querySelector('.apexcharts-yaxis-annotations text');
                 const dots = document.querySelectorAll('.apexcharts-series-markers circle, '
                                                      + '.apexcharts-series circle');
                 if (!label) return {label: false};
                 if (!dots.length) return {label: true, dots: 0};
                 const L = label.getBoundingClientRect();
                 let hits = 0;
                 dots.forEach(d => {
                   const D = d.getBoundingClientRect();
                   if (D.width === 0 && D.height === 0) return;
                   if (L.left < D.right && L.right > D.left &&
                       L.top < D.bottom && L.bottom > D.top) hits++;
                 });
                 return {label: true, dots: dots.length, hits: hits,
                         box: {t: Math.round(L.top), b: Math.round(L.bottom)}};
               }""")
    finally:
        browser.close()
        pw.stop()

    assert errors == [], "Battery health threw in the browser:\n  " + "\n  ".join(errors)
    assert overlap["label"], "no nominal annotation was drawn — the seed never reached the chart"
    assert overlap.get("dots"), "the chart drew no points — the seed never reached the chart"
    assert overlap["hits"] == 0, (
        f"the 'Nominal' label covers {overlap['hits']} of the {overlap['dots']} plotted charges "
        f"(#268)")


# ── #269 — the ring's total must be readable ─────────────────────────────────────────────────────
def test_the_charges_ring_prints_its_total_legibly(mate_url):
    page, browser, pw, status, errors = _open(mate_url, "/charges", "charges-donut.png")
    try:
        assert status == 200, f"Charges did not render: HTTP {status}"
        seen = page.evaluate(
            """() => {
                 const v = document.querySelector('.apexcharts-datalabel-value');
                 if (!v) return {value: false};
                 const fill = getComputedStyle(v).fill || v.getAttribute('fill');
                 const m = fill.match(/[\\d.]+/g) || [];
                 const [r, g, b] = m.map(Number);
                 // Relative luminance, the same quantity a contrast ratio is built from. The card
                 // behind it is #1e293b — luminance about 0.02 — so anything dark is unreadable
                 // there whatever its exact shade.
                 const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
                 return {value: true, text: v.textContent.trim(), fill: fill, lum: lum};
               }""")
    finally:
        browser.close()
        pw.stop()

    assert errors == [], "Charges threw in the browser:\n  " + "\n  ".join(errors)
    assert seen["value"], "the ring drew no centre value — the seed never reached the chart"
    assert seen["text"] == "8", f"the ring's total reads {seen['text']!r}, not the 8 seeded charges"
    assert seen["lum"] > 0.5, (
        f"the total in the middle of the ring is {seen['fill']} (luminance {seen['lum']:.2f}) on a "
        f"#1e293b card — that is the invisible number @adoewa photographed (#269)")
