"""Real-browser download semantics; optional outside the local browser test environment."""
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def test_export_download_and_pending_clicks_in_browser():
    # Close Playwright's sync event loop before the suite's asyncio.run-based tests.
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            _check_download(browser.new_page(accept_downloads=True))
        finally:
            browser.close()


def _check_download(page):
    page.route("http://mate.test/", lambda route: route.fulfill(body="""
      <a id="research-export" href="api/research/export" data-waiting="WAIT"
         data-busy="BUSY" data-error="ERROR" data-ready="READY">Download</a>
      <div id="research-export-status" role="status"></div>""", content_type="text/html"))
    pending = []
    page.route("**/api/research/export", lambda route: pending.append(route))
    page.goto("http://mate.test/")
    page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / "web/static/research-export.js"))
    page.locator("#research-export").click()
    page.wait_for_function("document.querySelector('[role=status]').textContent === 'WAIT'")
    # A real disabled anchor suppresses neither programmatic clicks nor keyboard activation;
    # the script's pending guard must handle both.
    page.locator("#research-export").dispatch_event("click")
    assert len(pending) == 1
    with page.expect_download() as download:
        pending.pop().fulfill(body=b"encrypted-test-envelope", content_type="application/octet-stream",
                              headers={"Content-Disposition": 'attachment; filename="mate-beta-bundle-123.matebeta"'})
    assert download.value.suggested_filename == "mate-beta-bundle-123.matebeta"
    assert Path(download.value.path()).read_bytes() == b"encrypted-test-envelope"
    page.wait_for_function("document.querySelector('[role=status]').textContent === 'READY'")
    page.locator("#research-export").click()
    page.wait_for_function("document.querySelector('[role=status]').textContent === 'WAIT'")
    pending.pop().fulfill(status=409)
    page.wait_for_function("document.querySelector('[role=status]').textContent === 'BUSY'")
    assert page.locator("#research-export").get_attribute("aria-disabled") is None
