"""Every kind the poller keeps has a group, an icon, a colour and a name for both ends, in every language.

The kinds are a closed list: poller/events.py's RULES and LEVELS. The page has no "Other" group to catch a kind
it does not know, so a new one arrives only with a change to the code, and this is what makes that
change complete. A stored row of a kind the page does not list — one an older build kept — is left
out without an error.
"""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import db_reader
import events as E
import pytest
from events_fixture import Car, event_row, grouped, rows, web

LOCALES = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es")


def test_the_page_lists_exactly_the_kinds_the_poller_keeps():
    assert set(db_reader.EVENT_SIGNAL_KINDS) == set(E.RULES) | set(E.LEVELS)
    assert set(db_reader.EVENT_LEVEL_KINDS) == set(E.LEVELS)


@pytest.mark.parametrize("lang", LANGS)
def test_each_kind_has_a_group_an_icon_a_colour_and_both_names(lang):
    strings = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))["translations"]
    for kind in db_reader.EVENT_KINDS:
        group = db_reader.EVENT_GROUP_OF[kind]
        assert db_reader.EVENT_ICONS[kind] and db_reader.EVENT_GROUP_COLORS[group], kind
        assert strings.get(f"events_kind_{kind}"), (lang, kind)
    for kind in E.RULES:
        assert strings.get(f"events_{kind}_on") and strings.get(f"events_{kind}_off"), (lang, kind)
        assert strings[f"events_{kind}_on"] != strings[f"events_{kind}_off"], (lang, kind)
    for kind in E.LEVELS:                                 # a level reached, and the bottom of the scale
        assert "{pct}" in strings.get(f"events_{kind}_level", ""), (lang, kind)
        assert strings.get(f"events_{kind}_off"), (lang, kind)
    for key in ("events_trip_started", "events_trip_ended", "events_charge_started", "events_charge_ended"):
        assert strings.get(key), (lang, key)


def test_a_stored_row_of_a_kind_not_listed_is_left_out(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    at = datetime.now(timezone.utc) - timedelta(hours=2)
    event_row(car, "locked_not_armed", at)
    event_row(car, "unlocked", at + timedelta(minutes=1))
    assert [r["kind"] for r in rows(grouped())] == ["unlocked"]
    assert client.get("/events").status_code == 200
