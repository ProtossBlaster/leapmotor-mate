"""A search's Back to calendar returns to the month the calendar showed when the search was made.

Trips and Charges search from a form that carries the month, for the results' Back to calendar. The form took it
from the page as first drawn, and the calendar's arrows and year buttons change the month without drawing the page
again: a search after them went back to the month the page had opened on. The form now follows the calendar.
"""
import pytest
from events_fixture import Car, serve, web
from test_a_charge_opened_from_a_trip_leads_back_to_it import _trip_with_a_charge_after_it

INGRESS = "/api/hassio_ingress/tok"


@pytest.mark.parametrize("page_name", ["trips", "charges"])
def test_back_to_calendar_returns_to_the_month_the_arrows_picked(tmp_path, monkeypatch, page_name):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _trip_with_a_charge_after_it(car)        # a page with no trips or charges yet shows no calendar
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client, INGRESS))
        page.goto(f"http://mate.test{INGRESS}/{page_name}")
        page.locator(f"#{page_name}-calendar-month button", has_text="◀").click()
        page.wait_for_url(lambda url: "month=" in url)
        month = page.locator("[data-cal-month]").get_attribute("data-cal-month")
        page.locator(f"#{page_name}-search-q").press_sequentially("nothing like it")
        page.locator(f"#{page_name}-calendar-month-wrap button", has_text="Back to calendar").click()
        page.wait_for_function(f"document.querySelector('[data-cal-month]')?.dataset.calMonth === {month!r}")
        assert errors == []
        browser.close()
