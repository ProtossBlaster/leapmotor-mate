"""The Events page opens in every language, every row of it translated, sits above Reports in the
menu, and does not reload itself.

A reload would drop what was typed into the search; the page refreshes nothing on its own, like
Charges and the Map. The menu entry comes right after Statistics, before Reports, in base.html.
"""
import html
import pathlib
import re
from datetime import datetime, timedelta, timezone

import events as E
import i18n
import pytest
from events_fixture import Car, event_row, web

LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es")
BASE = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" / "base.html"
PAGE = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" / "events.html"


@pytest.mark.parametrize("lang", LANGS)
def test_the_page_opens_in_every_language(tmp_path, monkeypatch, lang):
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang=lang)
    at = datetime.now(timezone.utc) - timedelta(hours=3)
    for kind in E.RULES:                                  # both ends of every kind, a climate start with its readings
        event_row(car, kind, at, inside_temp=17.0, climate_target_temp=21.0, outside_temp=8.0)
        event_row(car, kind, at + timedelta(minutes=5), state=0, inside_temp=21.0)
    for kind in E.LEVELS:                                 # a level reached, and the bottom of the scale
        event_row(car, kind, at, state=50)
        event_row(car, kind, at + timedelta(minutes=5), state=0)
    r = client.get("/events")
    assert r.status_code == 200
    t = i18n.get_t(lang)
    assert t("nav_events") in r.text and t("events_title") in r.text
    assert t("events_trunk_off") in html.unescape(r.text) and t("events_parked") in html.unescape(r.text)
    assert not re.findall(r"\b(?:events|temp|target|outside)_\w+", r.text), "every string translated"


def test_the_menu_entry_sits_between_statistics_and_reports():
    nav = BASE.read_text()
    assert nav.index("t('nav_statistics')") < nav.index("t('nav_events')") < nav.index("t('nav_report')")
    assert nav.count("t('nav_events')") == 1


def test_the_page_does_not_reload_itself():
    assert re.search(r"\{% block autorefresh %\}0\{% endblock %\}\{#.+#\}", PAGE.read_text()), \
        "autorefresh off, with the reason beside it"
