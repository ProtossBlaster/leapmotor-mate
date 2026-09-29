"""The status card's label/value rows keep a gap, in every language.

@jcconca, #340: in Spanish the Summary card's secondary rows read "Cuentakilómetros1930" with
"km" on a line of its own, and "Temperatura de consigna" / "24 °C" both wrapped. The rows are
`flex justify-between` half-width cells, which distributes the FREE space — and a label that
fills its cell leaves none, so the two texts touch and the value breaks.

Measured in a browser, not in the template: whether two boxes touch is a fact about the rendered
page at a given width, and the same markup is clean in English at the same width.

🔑 The value is what must never wrap. "1930 km" and "24 °C" are one reading; split over two lines
they read as two. The label may wrap — it is prose, and Spanish is simply longer.
"""
import pytest

_ROWS = "#status-card .grid.grid-cols-2 > div.flex"


def _spanish_status_rows(sync_api, tmp_path_factory, width):
    """Every label/value pair of the status card, as boxes on the page at `width`."""
    from web_in_a_browser import chromium, seed_database, served

    data = tmp_path_factory.mktemp("mate-340")
    db = data / ("leapmotor_mate" + ".db")
    seed_database(db, "LVIN0000000000001", rows=[
        ("INSERT INTO settings (key, value) VALUES ('language', 'es')", ()),
        ("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
        # One poll, so the card has an odometer, temperatures and a setpoint to print.
        ("INSERT INTO positions (vehicle_id, recorded_at, soc, odometer_km, speed_kmh, gear, "
         " inside_temp, outside_temp, climate_target_temp, battery_min_temp, charging, plug_connected) "
         " VALUES (1, '2026-09-29T10:00:00+00:00', 61.0, 148305, 0, 'P', 22.5, 18.0, 24.0, 20.0, 0, 1)", ()),
    ])
    measured = []
    with served(data, db) as url:
        pw, browser = chromium(sync_api)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            assert page.goto(url + "/").status == 200
            # The card arrives by htmx (hx-trigger="load"), so the rows do not exist yet when the
            # document does — wait for the content, never for a timeout.
            page.wait_for_selector(_ROWS, state="visible", timeout=15000)
            for row in page.locator(_ROWS).all():
                spans = row.locator("> span")
                if spans.count() < 2:
                    continue
                label, value = spans.nth(0), spans.nth(1)
                measured.append({
                    "label": label.inner_text().strip(),
                    "value": value.inner_text().strip(),
                    "label_box": label.bounding_box(),
                    "value_box": value.bounding_box(),
                    "value_lines": value.evaluate("el => el.getClientRects().length"),
                    # Boxes can sit side by side while the INK of an unbreakable word runs out of
                    # its own box and under the value — "Cuentakilómet148305 km" on screen with a
                    # measured gap of 12 px. Overflow is the thing to assert; the gap is not enough.
                    "label_overflow": label.evaluate(
                        "el => Math.round(el.scrollWidth - el.getBoundingClientRect().width)"),
                })
        finally:
            browser.close()
            pw.stop()
    assert measured, "the status card rendered no label/value row to measure"
    return measured


@pytest.fixture(scope="module")
def _browser():
    pytest.importorskip("fastapi", reason="web/main.py needs fastapi")
    pytest.importorskip("uvicorn", reason="the page has to be SERVED")
    return pytest.importorskip("playwright.sync_api",
                               reason="needs playwright + `playwright install chromium`")


@pytest.mark.parametrize("width", [381, 1149])
def test_no_spanish_label_touches_its_value(_browser, tmp_path_factory, width):
    """The two reported widths: the phone shot and the desktop shot in the issue."""
    for row in _spanish_status_rows(_browser, tmp_path_factory, width):
        lb, vb = row["label_box"], row["value_box"]
        assert lb and vb, f"row {row['label']!r} has no box"
        gap = vb["x"] - (lb["x"] + lb["width"])
        assert gap >= 4, (f"at {width} px, {row['label']!r} ends {-gap:.1f} px INTO its value "
                          f"{row['value']!r} (gap {gap:.1f} px, needs 4)")


@pytest.mark.parametrize("width", [320, 381, 420, 1149])
def test_a_label_stays_inside_its_own_box(_browser, tmp_path_factory, width):
    """A label of one long word cannot wrap by itself: `Cuentakilómetros` has nowhere to break, so
    shrinking its box only pushes the letters out of it and under the value."""
    for row in _spanish_status_rows(_browser, tmp_path_factory, width):
        assert row["label_overflow"] <= 1, (
            f"at {width} px, {row['label']!r} runs {row['label_overflow']} px out of its own box, "
            f"under the value {row['value']!r}")


@pytest.mark.parametrize("width", [381, 1149])
def test_a_reading_with_a_unit_stays_on_one_line(_browser, tmp_path_factory, width):
    for row in _spanish_status_rows(_browser, tmp_path_factory, width):
        assert row["value_lines"] == 1, (
            f"at {width} px, the value {row['value']!r} of {row['label']!r} is drawn on "
            f"{row['value_lines']} lines")
