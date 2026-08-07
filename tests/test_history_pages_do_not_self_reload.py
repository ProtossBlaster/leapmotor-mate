"""The pages that show the past must not reload themselves — #236, @michapr.

*"You have selected a time period for the statistics, are currently looking at the statistics —
then autorefresh occurs and reloads the page, all settings are gone."*

The cause is in the mechanism, not in his browser. base.html runs an idle-aware auto-refresh whose
final act is `location.reload()`, and on these two pages the thing the user chose is **not in the
address**: the month comes from `hx-get="api/trips/calendar?year=…&month=…"`, swapped into the page.
Reloading the address therefore restores the DEFAULT month, every time, mid-read.

⚠️ The existing idle guards were never going to catch this. They protect a field being typed in, an
interaction in the last 20 s, an in-flight request, an open merge dialog — a choice already MADE and
now being read looks exactly like an idle page. @pdifeo hit the same class in beta #22 and got the
`[data-holds-selection]` guard; this is the other half of it.

Both pages show history: there is nothing live for a reload to fetch, and the manual refresh button
sits in the header. Declared plainly, because it is a trade: a Trips page left open on a wall
display stops picking up a trip that just ended. Nobody has asked for that; @michapr has asked for
this.
"""
import re
import pathlib

import pytest

TPL = pathlib.Path(__file__).parents[1] / "web" / "templates"
HISTORY = ("trips.html", "statistics.html")
BLOCK = re.compile(r"{%\s*block\s+autorefresh\s*%}\s*(\d+)\s*{%\s*endblock\s*%}")


@pytest.mark.parametrize("page", HISTORY)
def test_a_history_page_declares_no_auto_reload(page):
    """Seen red before the change: both pages inherited base.html's 30 s default."""
    m = BLOCK.search((TPL / page).read_text(encoding="utf-8"))
    assert m, f"{page} must state its auto-refresh explicitly, not inherit the default"
    assert m.group(1) == "0", f"{page} still reloads itself every {m.group(1)}s"


@pytest.mark.parametrize("page", HISTORY)
def test_it_says_why(page):
    """A bare 0 invites the next person to 'tidy it up'. charges.html sets the house form: the
    number carries its reason on the same screen."""
    txt = (TPL / page).read_text(encoding="utf-8")
    i = txt.index("block autorefresh")
    assert "236" in txt[max(0, i - 700):i + 60], f"{page}: the 0 must name what it is for"


def test_the_default_is_still_thirty_seconds_for_everyone_else():
    """The fix is two pages opting out, NOT the mechanism being switched off for the whole app —
    that would silently freeze every page nobody complained about."""
    base = (TPL / "base.html").read_text(encoding="utf-8")
    assert 'data-autorefresh="{% block autorefresh %}30{% endblock %}"' in base


def test_the_pages_with_their_own_live_updates_are_untouched():
    """Overview and Charges were already 0 for their own reasons. If this change had reached them
    the diff would look identical and the reason would be lost."""
    for page in ("overview.html", "charges.html"):
        m = BLOCK.search((TPL / page).read_text(encoding="utf-8"))
        assert m and m.group(1) == "0", f"{page} changed meaning"


@pytest.mark.parametrize("page", ["battery.html", "trip_detail.html", "vehicle.html"])
def test_a_page_with_nothing_to_lose_still_refreshes(page):
    """The other side of the trade, and the reason the fix is TWO pages and not a global switch.

    These reload harmlessly: whatever they show comes from the address, so `location.reload()`
    rebuilds the same screen. trip_detail is the one worth naming — it looks like a history page,
    but the trip id is in the URL and its one piece of held state, the merge dialog, already has
    its own guard in base.html. Sweeping it up here would have frozen a page nobody complained
    about."""
    assert not BLOCK.search((TPL / page).read_text(encoding="utf-8")), \
        f"{page} must still inherit the default"
