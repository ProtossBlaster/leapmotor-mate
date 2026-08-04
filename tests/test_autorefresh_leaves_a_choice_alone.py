"""The auto-refresh must not reload while the user is part-way through a choice (@pdifeo, beta #22).

Every page reloads itself every 30 s unless it opts out. The guards were: a text field has focus,
the tab is hidden, the last interaction was under 20 s ago, an htmx request is in flight. None of
them knows about a *choice in progress* — and thinking is not idling. @pdifeo turned on merge mode
on the Trips page, weighed which of three trips to join, said nothing for twenty seconds, and the
reload took merge mode, the gap he had set and the open day with it:

    "se si pensa troppo la pagina fa refresh resettando la visualizzazione e bisogna
     ripensare di nuovo :-)"

Two new guards: a `data-holds-selection` marker anywhere on the page, and the merge dialog being
open. The marker sits on the merge block itself, so leaving the mode removes it — there is no
"remember to clear the flag" step that a later edit could forget.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = (ROOT / "web" / "templates" / "base.html").read_text()
DAY = (ROOT / "web" / "templates" / "partials" / "trips_calendar_day_content.html").read_text()
TRIPS = (ROOT / "web" / "templates" / "trips.html").read_text()


def _refresh_block() -> str:
    """The body of the setInterval that reloads the page."""
    return BASE.split("setInterval(() => {", 1)[1].split("}, secs * 1000);", 1)[0]


def test_the_trips_page_really_does_auto_refresh():
    """The premise. If Trips ever opts out, these guards stop being load-bearing and this file
    should be revisited rather than passing for the wrong reason."""
    assert "{% block autorefresh %}" not in TRIPS, "Trips now sets its own value — re-check this"
    assert 'data-autorefresh="{% block autorefresh %}30{% endblock %}"' in BASE


def test_an_unfinished_selection_stops_the_reload():
    assert "document.querySelector('[data-holds-selection]')" in _refresh_block()


def test_an_open_merge_dialog_stops_the_reload():
    block = _refresh_block()
    assert "getElementById('merge-modal')" in block
    assert "getComputedStyle(dlg).display !== 'none'" in block, \
        "the modal is shown by flipping display, not by an [open] attribute"


def _attr_count(html: str) -> int:
    """Occurrences of the ATTRIBUTE, not of the word. The first version of this test counted the
    string anywhere and passed happily with the attribute deleted — it was matching the comment
    that explains it."""
    return len(re.findall(r'<[^>]*\sdata-holds-selection[\s>=]', html))


def test_merge_mode_carries_the_marker():
    assert _attr_count(DAY) == 1, "the marker must be on a real element, not only in a comment"


def test_the_marker_lives_inside_the_merge_block_so_it_clears_itself():
    """If it sat outside `{% if merge_mode %}` it would persist after leaving the mode and quietly
    disable the refresh for the rest of the visit."""
    before, after = DAY.split("{% if merge_mode %}", 1)
    assert _attr_count(after) == 1
    assert _attr_count(before) == 0


def test_the_old_guards_are_still_there():
    """These were doing real work — the new ones are additions, not replacements."""
    block = _refresh_block()
    assert "document.hidden" in block
    assert "lastInteraction < 20000" in block
    assert "'.htmx-request'" in block
