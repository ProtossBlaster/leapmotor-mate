"""The two toggles over the idle-drain chart must not use the same word for different things.

There are two independent controls there. One picks the UNIT — a rate normalised to 24 hours
(`%/day`) or the SoC actually lost (`%`). The other picks the GROUPING — one bar per park, or
every park that began on the same calendar date added together.

While both said "day", the strip read `%/day | % lost` beside `per stop | per day`, and the
obvious reading was that the second pair chose the normalisation. It does not; the normalisation
is the first pair and it is on by default. @riri19 reported it twice in one week (#154, then #179)
and was right both times — the numbers were never wrong, the labels were.

Checked per language against that language's own word for "day", because the collision is a
translation problem: getting it right in English and leaving "pro Tag" beside "%/Tag" in German
would rebuild exactly the same trap for German readers.
"""
import json
import pathlib

LOCALES = sorted((pathlib.Path(__file__).resolve().parent.parent / "web" / "locales").glob("*.json"))


def _t(p):
    return json.load(open(p))["translations"]


def test_there_are_locales_to_check():
    assert len(LOCALES) >= 6, [p.name for p in LOCALES]


def test_the_grouping_button_never_repeats_the_unit_buttons_word_for_day():
    """The falsifiable core: before the fix every language failed this, because the grouping
    button was literally "per <day>" in each of them."""
    for p in LOCALES:
        t = _t(p)
        day = t["day_unit"].strip().lower()
        group = t["battery_vampire_per_day"].strip().lower()
        assert day and day not in group, (
            f"{p.name}: grouping label {t['battery_vampire_per_day']!r} contains the unit "
            f"toggle's own word for day ({t['day_unit']!r}) — the two controls read as one")


def test_the_two_grouping_options_still_say_different_things():
    for p in LOCALES:
        t = _t(p)
        assert t["battery_vampire_per_park"].strip().lower() != t["battery_vampire_per_day"].strip().lower()


def test_the_help_text_still_explains_the_normalisation():
    """The rate is what most people actually want and it is the default, so the help has to keep
    saying so — renaming a button must not quietly leave the explanation behind."""
    for p in LOCALES:
        t = _t(p)
        assert t["day_unit"].strip().lower() in t["battery_vampire_help"].lower(), p.name
