"""The calendar grids must show WHICH DAY the list below belongs to.

A day cell fetches only the drawer underneath it — never the grid — so the server never gets
to re-render the cells and cannot mark the choice. Before this, the amber "today" ring sat
unmoved while the list below showed some other day entirely (reported on the Trips calendar,
present identically on Charges and Wallbox: one commit added all three from one template).

These tests are written against the FILES rather than a rendered page on purpose. The defect
was never that one calendar was wrong — it was that three copies of the same markup existed
and the hook had to reach every one of them. Discovering the partials by glob means a fourth
calendar added later is covered the day it appears, instead of quietly repeating the bug.
"""
import pathlib

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
CALENDARS = sorted((TEMPLATES / "partials").glob("*_calendar_month.html"))
BASE = (TEMPLATES / "base.html").read_text()


def test_there_are_calendars_to_check():
    """Guards the glob itself: if the partials are ever renamed, the tests below would all
    pass vacuously over an empty list and stop protecting anything."""
    assert len(CALENDARS) >= 3, [p.name for p in CALENDARS]


def test_every_clickable_day_cell_carries_the_hook():
    missing = [p.name for p in CALENDARS if "cal-day" not in p.read_text()]
    assert not missing, f"calendars whose day cells cannot be marked: {missing}"


def test_the_hook_is_on_the_cell_that_opens_the_drawer():
    """Not merely present somewhere in the file — on the element that is actually clicked.
    The empty days are <div>s with no handler and must not be selectable."""
    for p in CALENDARS:
        for line in p.read_text().splitlines():
            if "cal-day" in line:
                assert "aspect-square" in line, f"{p.name}: cal-day is not on the day cell"
                break
        else:
            raise AssertionError(f"{p.name}: no cal-day at all")


def test_the_stylesheet_can_beat_the_inline_border_colour():
    """The cell paints border-color in a style attribute, which an ordinary rule loses to —
    so the selected-day rule has to carry !important or it silently does nothing."""
    assert ".cal-day[data-selected]" in BASE
    rule = BASE.split(".cal-day[data-selected]", 1)[1].split("}", 1)[0]
    assert "border-color" in rule and "!important" in rule


def test_the_click_handler_exists_and_clears_the_previous_choice():
    """Marking without unmarking would leave every day you ever clicked ringed."""
    assert "closest('.cal-day')" in BASE
    assert "removeAttribute('data-selected')" in BASE
    assert "setAttribute('data-selected', '')" in BASE


# ── surviving the 30-second auto-refresh ─────────────────────────────────────────
#
# The ring alone was only half the report: these pages reload themselves every 30 seconds
# when idle, and a reload came back with the ring gone AND the list emptied. The day is kept
# for the tab session and put back on the calendar's own request, so grid and drawer arrive
# together.

MAIN = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()


def test_every_calendar_renderer_passes_the_open_day_to_its_grid():
    """The drawer already opened on `open_day`; without it in the context too, the grid has
    nothing to ring — which is also what a ?highlight= link used to look like."""
    assert MAIN.count('ctx["open_day"] = open_day') == 3, "expected trips, charges and wallbox"


def test_every_grid_rings_the_day_its_drawer_is_showing():
    for p in CALENDARS:
        s = p.read_text()
        assert "{% set is_open =" in s, f"{p.name}: no open-day check"
        assert "{% if is_open %} data-selected{% endif %}" in s, f"{p.name}: the cell is never marked"


def test_the_chosen_day_is_remembered_for_the_tab_session():
    assert "sessionStorage.setItem(KEY" in BASE
    assert "'lm_calday_' + location.pathname" in BASE


def test_the_remembered_day_is_put_back_on_the_calendar_request():
    assert "htmx:configRequest" in BASE
    assert "e.detail.parameters.open_day = parts[2]" in BASE


def test_the_month_is_read_off_the_path_not_off_the_parameters():
    """htmx leaves a query string written into hx-get in the path and never copies it into
    `parameters`. Reading the month from `parameters` therefore makes every request look like
    the current month, and paging back one month would open whichever day shares that number.
    The guard is only as good as where it reads the month from."""
    hook = BASE.split("htmx:configRequest", 1)[1].split("});", 1)[0]
    assert "exec(path)" in hook, "the month is not being read from the request path"
    assert "e.detail.parameters.year" not in hook
    assert "e.detail.parameters.month" not in hook


def test_a_request_that_already_names_a_day_is_left_alone():
    """A ?highlight= link builds its own open_day into the grid URL; overwriting it with a
    stale remembered day would send someone to the wrong day from a link they just followed."""
    hook = BASE.split("htmx:configRequest", 1)[1].split("});", 1)[0]
    assert "open_day=" in hook and "return" in hook
