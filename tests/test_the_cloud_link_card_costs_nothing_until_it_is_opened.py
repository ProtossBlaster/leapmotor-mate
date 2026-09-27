"""Settings must not pay for a card nobody opened, and the link tile must not scan a growing table.

27/09/2026, within hours of 4.5.0: "tutto è lentissimo da caricare … come add-on il problema è
ancora più grave". Measured on the published images, same seeded database, median of five:

    Settings   4.4.0  34 ms  ·  4.5.0  103 ms empty poll_log  ·  4.5.0  136 ms with a week of rows

`polling_summary()` was computed inside `settings_page`, on every load, whether or not the Cloud
link card was open — 288 five-minute windows and seven days of counts, aggregated in Python from
every row of the last eight days. On an add-on running off an SD card that is the difference
between a page and a wait.

Two things are held here:

  * the Settings page does not compute the summary. The card fetches its own body when it is
    opened, like the status strips already do on their own trigger.
  * the link tile's two "last poll" lookups are bounded by time, so they use idx_poll_log_at
    instead of walking the table to its end. The healthy case is the one that walks: with no
    failure to find, `last_bad` reads every row there is.
"""
import inspect

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


def test_the_settings_page_does_not_build_the_polling_summary():
    import main
    source = inspect.getsource(main.settings_page)
    assert "polling_summary" not in source, (
        "Settings computes the polling summary on every load; it belongs to the card's own fetch"
    )


def test_the_card_has_an_endpoint_of_its_own():
    import main
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    assert "/api/polling-card" in paths, f"no endpoint for the card to fetch: {sorted(p for p in paths if 'poll' in p)}"


def test_the_settings_template_fetches_the_card_instead_of_rendering_it():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "web/templates/settings.html").read_text(encoding="utf-8")
    card = html.split("cloud_link", 1)[1].split("{% endcall %}", 1)[0]
    assert "api/polling-card" in card, "the card does not fetch its own body"
    assert "polling.strip" not in card, "the card still renders the strip inline, so Settings pays for it"


def test_the_last_poll_lookups_are_bounded_by_time():
    import db_reader
    source = inspect.getsource(db_reader._link_details)
    lookups = [line for line in source.splitlines() if "FROM poll_log" in line]
    assert len(lookups) == 2, f"expected the two last-poll lookups, found {len(lookups)}"
    assert source.count("at >= ?") >= 2, (
        "the lookups are not bounded: with nothing to find they walk the whole table"
    )
