"""The Overview must not carry an OTA row, because on the setup we recommend it can only lie.

The row read "OTA updates — None" whenever the account inbox held no update message. It was never
a statement about the car: Leapmotor exposes the installed and waiting versions only to the account
that OWNS the car, and the inbox of an account the car is merely SHARED with receives no vehicle
notices at all — only sharing invitations. So on that setup the row says "None" for ever, and "None"
reads as "you are up to date".

And the shared account is not an edge case, it is the arrangement: the only owner account is the one
in the official app on the owner's phone, and Leapmotor binds a session per device, so Mate runs on
a shared account by design. Silvio, 28/09/2026, looking at the row: *«io direi di toglierlo, per
sugli account condivisi non funziona. e non possiamo costringere tutti gli utenti ad usare l'account
primario e non quello condiviso»*.

Measured the same day against the real cloud, on his own B10 through the shared account Mate runs
on: the vehicle-update endpoint answers `code [40, 40]` — the cloud declining to tell a
non-owner. → PR #335, which proposed to put the version there instead.

What stays: the OTA Update Notice entity in Home Assistant (#277, @HaJeeEs). It does not lie — it is
simply OFF when no update message arrived — and taking an entity out of every installation would
break whatever automation was built on it.
"""
import collections
import pathlib

import pytest

jinja2 = pytest.importorskip("jinja2")


def _render_card(**status):
    class Quiet(jinja2.Undefined):
        """Everything the card needs but this test has no opinion about renders as nothing."""
        def __call__(self, *a, **k): return ""
        def __str__(self): return ""
        def __getattr__(self, _): return Quiet()

    class AnyFilter(dict):
        def __missing__(self, _): return lambda v, *a, **k: v

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(
        str(pathlib.Path(__file__).parents[1] / "web" / "templates")),
        autoescape=True, undefined=Quiet)
    env.filters = AnyFilter(env.filters)
    full = collections.defaultdict(lambda: None, {"soc": 50.0, **status})
    return env.get_template("partials/status_card.html").render(
        status=full, absent_temps=lambda: set(), t=lambda k: k, odometer_km=0,
        ago=lambda *a: "", state_color=lambda *a: "", car_resp=None, dist_val=lambda v: v,
        dist_unit=lambda *a: "km", speed_val=lambda v: v, speed_unit=lambda *a: "km/h",
        battery_price=None, currency="€", soc=None, color="", state="")


def test_the_card_has_no_ota_row_when_the_inbox_is_empty():
    out = _render_card(ota={"available": False, "title": None, "time": None})
    for key in ("ota_label", "ota_none"):
        assert key not in out, f"the OTA row is still on the card ({key}) — 'None' reads as 'up to date'"


def test_the_card_has_no_ota_row_even_when_a_notice_arrived():
    """The notice still reaches Home Assistant; the Overview no longer claims to know."""
    out = _render_card(ota={"available": True, "title": "Software update", "time": "2026-09-23"})
    for key in ("ota_label", "ota_available", "ota_none"):
        assert key not in out, f"the OTA row is still on the card ({key})"
    assert "Software update" not in out, "the notice title is still printed on the Overview"


@pytest.mark.parametrize("locale", ["en", "it", "de", "es", "fr", "nl", "pl", "pt-PT"])
def test_no_language_still_carries_the_removed_keys(locale):
    """A key left behind in one file out of eight is how a raw `ota_none` reaches a screen."""
    text = (pathlib.Path(__file__).parents[1] / "web" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
    for key in ("ota_label", "ota_none", "ota_available"):
        assert f'"{key}"' not in text, f"{locale}.json still defines {key}"
