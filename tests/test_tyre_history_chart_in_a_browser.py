"""The tyre-pressure chart on the Vehicle page, drawn in a real browser (D#483).

A tyre's day runs 2.40–2.55 bar: the band chart printed its scale with whole numbers only, and every
tick read «2». With `axisDec` it prints «2.40», and the scale's steps are ones that print exactly at
that many decimals; a chart that asks for neither keeps the whole steps it always had. The hover box
gives each tyre's lowest – highest of the day, not one of the two.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi")
from events_fixture import Car, serve, web


DAYS = ((2, 2.40), (2, 2.55), (1, 2.45), (1, 2.50))


def _open(tmp_path, monkeypatch, p, days=DAYS, lang="en", rr=2.5):
    import main
    car = Car(tmp_path)
    noon = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    for ago, fl in days:
        car.db._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, tire_fl_bar, tire_fr_bar, tire_rl_bar, tire_rr_bar)"
            " VALUES (1,?,?,2.5,2.5,?)", ((noon - timedelta(days=ago)).isoformat(), fl, rr))
    car.db._conn.commit()
    import db_reader
    # The language the web read is remembered for the process: put back what was there, or the next
    # test in this worker (test_units) formats numbers in Italian.
    monkeypatch.setattr(db_reader, "_lang_memo", [None])
    client = web(car, monkeypatch, zone="UTC", lang=lang)
    monkeypatch.setattr(main.command_client, "get_fresh_signals", lambda: None)
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1000, "height": 900})
    # Counts the charts destroyed, to tell a switch of period that drops the old one from one that leaks it.
    page.add_init_script("window.destroyed = 0; document.addEventListener('DOMContentLoaded', () => {"
                         " const d = ApexCharts.prototype.destroy;"
                         " ApexCharts.prototype.destroy = function () { window.destroyed++; return d.apply(this, arguments); }; });")
    page.route("**/api/car-picture*", lambda r: r.abort())        # never the cloud
    page.route("**/*", serve(client))
    page.goto("http://mate.test/vehicle")
    page.locator("#tyre-history-chart svg").first.wait_for()
    return browser, page


def test_the_scale_prints_with_decimals_and_whole_charts_are_unchanged(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser, page = _open(tmp_path, monkeypatch, p)
        labels = page.eval_on_selector_all("#tyre-history-chart .apexcharts-yaxis-label",
                                           "els => els.map(e => (e.querySelector('tspan') || e).textContent.trim()).filter(Boolean)")
        assert "2.40" in labels and "2.60" in labels, labels
        assert page.evaluate("mateBandChart.niceScale(2.4, 2.55, 0.2, 2)") == [2.3, 2.7]
        # Without decimals: the whole steps it always gave (the charge and trip charts' scales).
        assert page.evaluate("mateBandChart.niceScale(3, 9, 4)") == [2, 10]
        assert page.evaluate("mateBandChart.niceScale(0, 7.3, 4)") == [0, 8]
        # 2.3 - 2.1 is 0.1999… in floating point: not a reason to double the scale.
        assert page.evaluate("mateBandChart.niceScale(2.1, 2.3, 0.2, 2)") == [2.1, 2.3]
        browser.close()


def test_the_hover_box_gives_the_days_lowest_and_highest(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser, page = _open(tmp_path, monkeypatch, p)
        box = page.locator("#tyre-history-chart").bounding_box()
        page.mouse.move(box["x"] + 60, box["y"] + box["height"] / 2)   # the first day, 2.40–2.55
        tip = page.locator("#tyre-history-chart .apexcharts-tooltip")
        tip.filter(has_text="–").wait_for()
        assert "2.40 – 2.55 bar" in tip.inner_text()
        browser.close()


def test_a_day_alone_between_two_holes_is_seen(tmp_path, monkeypatch):
    """A car not polled for days leaves single days between holes: a line needs two points, so each
    such day is drawn as a dot of its own — today's too, the last on the axis (final review, #2)."""
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser, page = _open(tmp_path, monkeypatch, p, days=((4, 2.40), (2, 2.45), (0, 2.50)))
        dots = page.evaluate("""() => [...document.querySelectorAll('#tyre-history-chart circle')]
            .filter(c => parseFloat(c.getAttribute('r')) >= 2 && c.getAttribute('fill') === '#fbbf24').length""")
        assert dots >= 3, dots          # front left's three days, each a visible dot
        browser.close()


def test_the_numbers_take_the_languages_decimal_mark(tmp_path, monkeypatch):
    """The live tyre card above the chart writes «2,5 bar» in Italian (units.decimal_point): the
    chart under it writes its scale and its hover box the same way, not «2.40»."""
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser, page = _open(tmp_path, monkeypatch, p, lang="it")
        labels = page.eval_on_selector_all("#tyre-history-chart .apexcharts-yaxis-label",
                                           "els => els.map(e => (e.querySelector('tspan') || e).textContent.trim()).filter(Boolean)")
        assert "2,40" in labels and not any("." in l for l in labels), labels
        box = page.locator("#tyre-history-chart").bounding_box()
        page.mouse.move(box["x"] + 60, box["y"] + box["height"] / 2)
        tip = page.locator("#tyre-history-chart .apexcharts-tooltip")
        tip.filter(has_text="–").wait_for()
        assert "2,40 – 2,55 bar" in tip.inner_text()
        browser.close()


def test_switching_the_period_drops_the_old_chart(tmp_path, monkeypatch):
    """htmx swaps the card for a new one: the old chart is destroyed, not left with its listeners."""
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser, page = _open(tmp_path, monkeypatch, p)
        before = page.evaluate("window.destroyed")
        page.get_by_role("button", name="90 days").click()
        page.locator("button[aria-pressed=true]", has_text="90 days").wait_for()
        page.locator("#tyre-history-chart svg").first.wait_for()
        assert page.evaluate("window.destroyed") == before + 1
        browser.close()


def test_a_tyre_the_car_never_sends_has_no_legend_and_the_others_draw(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser, page = _open(tmp_path, monkeypatch, p, rr=None)
        shown = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll(
            '#tyre-history-legend [data-series]')].map(b => [b.dataset.series, b.style.display !== 'none']))""")
        assert shown == {"fl": True, "fr": True, "rl": True, "rr": False}, shown
        paths = page.evaluate("document.querySelectorAll('#tyre-history-chart path.apexcharts-line').length")
        assert paths >= 6, paths        # three tyres, each a solid and a dashed line
        browser.close()
