"""The drive-mode tag offers what the cars actually have (#180 @adoewa).

The cloud never reports drive mode, so this is a label the driver attaches by hand. Mate shipped
three values — comfort, normal, sport — and a photograph of a C10's own screen (a MY2026 full
electric) shows four: ECO · Comfort · Sport · Custom, with no "normal" anywhere. @gm27271 reports
Sport · Normal · Individual on his range-extender. Two cars, two lists, ours matching neither.

One union list rather than a per-model table: an entry a given car doesn't have costs nothing on a
manual label, a missing one is the bug that got reported, and removing "normal" would orphan every
trip already tagged with it.

The list exists in FOUR places — the tuple below (which is also the validator) and three templates.
A value that validates but has no <option>, or an <option> the validator rejects, both end the same
way: the driver picks a mode, saves, and it silently disappears. That is what these tests are for.
"""
import json
import pathlib
import re

import db_reader

TEMPLATES = {
    "web/templates/trip_detail.html": "trip.drive_mode",       # the per-trip picker
    "web/templates/trips.html": None,                          # the search filter
    "web/templates/settings.html": "default_drive_mode",       # the default for new trips
}


def _options(path: str) -> list[str]:
    """The drive-mode <option> values in a template, in the order a person sees them."""
    html = pathlib.Path(path).read_text()
    block = re.search(r"mode_eco.*?mode_custom", html, re.S)
    assert block, f"{path}: no drive-mode option block"
    return re.findall(r'<option value="([a-z]+)"', html[max(0, block.start() - 400):block.end() + 200])


def test_the_photographed_modes_are_all_offered():
    # ECO and Custom are the two the C10's screen has and Mate didn't.
    for mode in ("eco", "comfort", "sport", "custom"):
        assert mode in db_reader.DRIVE_MODES, mode


def test_normal_is_kept_so_existing_tags_survive():
    """Dropping it would leave every trip already tagged 'normal' pointing at a value the picker no
    longer offers — the tag would read as blank and the driver would think Mate lost it."""
    assert "normal" in db_reader.DRIVE_MODES


def test_every_offered_mode_validates():
    """save_trip_note filters through DRIVE_MODES: an <option> missing from the tuple is a mode you
    can pick and cannot save."""
    for path in TEMPLATES:
        for value in _options(path):
            if value:
                assert value in db_reader.DRIVE_MODES, f"{path}: <option value={value}> is rejected on save"


def test_every_template_offers_the_whole_list():
    """…and the converse: a value the tuple accepts but no template shows can never be chosen."""
    for path in TEMPLATES:
        shown = set(_options(path))
        missing = set(db_reader.DRIVE_MODES) - shown
        assert not missing, f"{path} is missing {sorted(missing)}"


def test_all_six_languages_name_the_new_modes():
    for p in sorted(pathlib.Path("web/locales").glob("*.json")):
        d = json.loads(p.read_text())["translations"]
        for mode in db_reader.DRIVE_MODES:
            key = f"mode_{mode}"
            assert key in d and d[key].strip(), f"{p.name}: {key}"
