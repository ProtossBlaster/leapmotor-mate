"""Every string about the sunshade uses the name the official Leapmotor app gives it.

Outside Italian (4.11.1), the Vehicle and Commands tiles called it the panoramic roof, and the
confirmations, the events and the notice shown while driving used other names for it: German had
four. The names are the ones @arekm listed from the app in #391. Polish declines the name, so there
it is matched on its two stems; on the Vehicle tile it carries a soft hyphen, which no one reads
(test_the_vehicle_tiles_fit_a_phone_in_a_browser.py says why).
"""
import i18n
import pytest

APP = {"en": "Sunshade", "it": "Parasole", "fr": "Pare-soleil", "de": "Sonnenblende", "es": "Parasol",
       "nl": "Zonnescherm", "pl": "Osłona przeciwsłoneczna", "pt-PT": "Cortina"}
STEMS = {"pl": ("osłon", "przeciwsłoneczn")}
NAMES_IT = ("sunshade_moving", "open_shade_confirm", "close_shade_confirm", "events_kind_sunshade_level",
            "events_cmd_open_sunshade", "events_cmd_close_sunshade", "events_sunshade_level_level",
            "events_sunshade_level_off")


@pytest.mark.parametrize("lang", sorted(APP))
def test_both_tiles_carry_the_apps_name(lang):
    t = i18n.get_t(lang)
    shown = [t(key).replace("\u00ad", "") for key in ("win_sunshade", "sunshade")]
    assert shown == [APP[lang], APP[lang]]


@pytest.mark.parametrize("lang", sorted(APP))
def test_every_other_string_says_the_same_name(lang):
    t = i18n.get_t(lang)
    stems = STEMS.get(lang, (APP[lang].lower(),))
    assert [t(key) for key in NAMES_IT if not all(s in t(key).lower() for s in stems)] == []
