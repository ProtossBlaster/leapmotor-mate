""""Last seen" must mean when the CAR reported, not when we wrote the row — #232.

The poller writes a position row every 30 s while parked, and it keeps writing them while the cloud
re-serves one frozen frame (only DRIVING skips repeats). So the row is always seconds old, whatever
the data in it. Both places that print "last seen" used that row time:

    Overview map popup     "C10 — 22 seconds ago"     over a marker sixteen hours stale
    Status card            "22 seconds ago"           with an amber note only while moving

@rop12770 was looking at exactly that popup in #232 while asking why his car was in the sea.

🔑 The true age was already computed and already tested — `data_age_s`, from the frame's own
timestamp, measured on every poll even when nothing is announced (test_data_age.py pins that). It
simply never reached the page. Nothing new is calculated here; a figure that was wrong now shows the
number Mate already had.

⚠️ The amber stays a MARK on that one figure, not a second number beside it: the case worth
flagging is still "the link died while the car was moving" (#178), and two correct numbers under
one label read as a defect even when both are right.
"""
import re
import pathlib

import pytest

WEB = pathlib.Path(__file__).parents[1] / "web"
OVERVIEW = (WEB / "templates" / "overview.html").read_text()
CARD = (WEB / "templates" / "partials" / "status_card.html").read_text()


def _popup() -> str:
    """The bindPopup call, and nothing else — the comment above it names last_seen_s on purpose."""
    i = OVERVIEW.index(".bindPopup(")
    return OVERVIEW[i:OVERVIEW.index("\n", i)]


def _last_seen_block() -> str:
    """The <span> that prints the figure, anchored on the label above it."""
    i = CARD.index("t('last_seen')")
    return CARD[i:CARD.index("</div>", i)]


# ── the figure itself ─────────────────────────────────────────────────────────

def test_the_map_popup_dates_the_frame_not_the_row():
    """Seen red against `ago(status.last_seen_s)`."""
    p = _popup()
    assert "status.data_age_s" in p, "the popup must date the car's own frame"


def test_the_status_card_dates_the_frame_not_the_row():
    b = _last_seen_block()
    assert "status.data_age_s" in b


@pytest.mark.parametrize("name,block", [("popup", _popup()), ("card", _last_seen_block())])
def test_the_row_time_survives_only_as_the_fallback(name, block):
    """A car that reports no clock has no frame timestamp, and there the row time is all we have —
    dropping it would turn a slightly wrong answer into no answer."""
    assert "status.last_seen_s" in block, f"{name}: keep the fallback"
    assert re.search(r"data_age_s[^}]*if[^}]*else[^}]*last_seen_s", block), \
        f"{name}: the row time must come AFTER the else, not be the value shown"


# ── and the amber must not become a second number ────────────────────────────

def test_the_card_prints_the_age_once():
    """🔴 Two correct numbers under one label is the defect this file must not create: the old
    markup printed `ago(last_seen_s)` and then `data_age` beside it. Count the calls."""
    assert _last_seen_block().count("ago(") == 1, "the age must appear once"
    assert "t('data_age')" not in _last_seen_block(), \
        "the amber is a mark on the figure, not a second figure"


def test_the_warning_colour_still_fires_on_a_link_that_died_while_moving():
    """The amber itself must survive the rewrite — it is the only thing that separates 'stale
    because parked and asleep' from 'stale because the link dropped mid-drive' (#178)."""
    b = _last_seen_block()
    assert "status.data_age" in b and "amber" in b
    assert "data_age_hint" in b, "and it must keep its explanation"


# ── and what the page actually SAYS ──────────────────────────────────────────

def _render_card(**status):
    """The real partial, rendered. A source-string check proves the markup mentions a variable; only
    rendering proves which number a reader sees. `ago` is restated here rather than imported —
    web/main and poller/main share a name, and importing one to reach a filter drags in the other."""
    import jinja2

    class Quiet(jinja2.Undefined):
        """Everything the card needs but this test does not care about renders as nothing. Naming
        each helper instead would make the fixture a list of the card's decorations, and every new
        one would break a test that has no opinion about it."""
        def __call__(self, *a, **k): return ""
        def __str__(self): return ""
        def __getattr__(self, _): return Quiet()

    class AnyFilter(dict):
        """Same reasoning for the filters — `undefined` does not cover those, and an unknown one
        should pass its value straight through rather than fail a test about a different line."""
        def __missing__(self, _): return lambda v, *a, **k: v

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(WEB / "templates")),
                             autoescape=True, undefined=Quiet)
    env.filters = AnyFilter(env.filters)

    def ago(seconds):
        if seconds is None:
            return "—"
        s = max(int(seconds), 0)
        return f"{s}s ago" if s < 60 else (f"{s // 60}m ago" if s < 3600 else f"{s // 3600}h ago")

    import collections
    # …and the same for the status dict: the card reads two dozen keys, and this test has an
    # opinion about exactly two of them.
    full = collections.defaultdict(lambda: None, status)
    return env.get_template("partials/status_card.html").render(
        status=full, ago=ago, t=lambda k: k, state_color=lambda *a: "", car_resp=None,
        dist_val=lambda v: v, dist_unit=lambda *a: "km", speed_val=lambda v: v,
        speed_unit=lambda *a: "km/h", battery_price=None, currency="€", soc=None,
        color="", state="")


def test_a_frozen_frame_is_not_called_twenty_two_seconds_old():
    """@rop12770's screen, reproduced: the row is 22 s old, the frame behind it is 16 hours old."""
    out = _render_card(last_seen_s=22, data_age_s=57_600, data_age=None)
    assert "16h ago" in out, "the reader must be told how old the DATA is"
    assert "22s ago" not in out, "the row's age must not be presented as the car's"


def test_a_healthy_car_reads_the_same_as_before():
    """The ordinary case: frame and row seconds apart, nothing changes on screen."""
    out = _render_card(last_seen_s=12, data_age_s=14, data_age=None)
    assert "14s ago" in out


def test_a_car_that_reports_no_clock_still_gets_an_answer():
    out = _render_card(last_seen_s=45, data_age_s=None, data_age=None)
    assert "45s ago" in out, "with no frame timestamp the row time is all we have"
