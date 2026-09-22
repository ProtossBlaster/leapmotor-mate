"""A charge can never put more into the battery than the wall gave it (#295 @gm27271).

His wallbox page showed one session at **7.69 kWh AC · 10.03 kWh DC · 130.4 %**, and painted the
130.4 % GREEN — the same colour it uses for an excellent charge, because the macro only asks
whether the number is ≥ 88. The charge card had known better since it was written: it computes the
same ratio and hides it above 100 %, with a comment saying why. Two screens, two policies, and the
one that spoke was the one that was wrong.

So the rule moves to where the arithmetic already lives — `wallbox_session_energy` and
`wallbox_ac_dc_totals`, the pair whose own docstring records what it cost to have this in two
places (#229). The two kWh figures stay on screen: they are what was measured, and a reader
comparing them is exactly how this got reported. Only the ratio, which claims the two are
comparable, is withheld.

⚠️ NOT a decision about which number to believe. Nothing stored is touched and no meter figure is
thrown away — `poller/db.py` is deliberate that a DC figure resting on a battery capacity the user
types in must never discredit a measured one. This is about what a page may assert.
"""
import pathlib

import db_reader

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _row(ac, dc):
    return {"ac_energy_kwh": ac, "energy_added_kwh": dc}


# ── one session ──────────────────────────────────────────────────────────────

def test_his_session_keeps_both_figures_and_withholds_the_ratio():
    """#295 exactly, from the stored columns."""
    s = db_reader.wallbox_session_energy(_row(ac=7.69, dc=10.03))
    assert s["ac_kwh"] == 7.69, "what the meter measured stays on screen"
    assert s["dc_kwh"] == 10.03, "and so does what the battery took"
    assert s["eff"] is None, "but 130.4 % is not a claim this page may make"


def test_a_normal_session_still_gets_its_percentage():
    s = db_reader.wallbox_session_energy(_row(ac=11.2, dc=10.0))
    assert s["eff"] == 89.3


def test_the_edge_is_kept_at_exactly_100():
    """Equal figures are a coarse meter, not an impossibility — the card's own rule is `<= 100`."""
    assert db_reader.wallbox_session_energy(_row(ac=10.0, dc=10.0))["eff"] == 100.0


# ── a set of them ────────────────────────────────────────────────────────────

def test_a_total_that_comes_out_impossible_withholds_its_ratio_too():
    """Rolled up, the same nonsense can appear on a day, a month or a year node."""
    t = db_reader.wallbox_ac_dc_totals([_row(ac=7.69, dc=10.03), _row(ac=3.0, dc=4.2)])
    assert t["ac"] == 10.69 and t["dc"] == 14.23, "the kWh still add up"
    assert t["eff"] is None
    assert t["counted"] == 2, "and both charges are still counted"


def test_a_healthy_total_is_unchanged():
    t = db_reader.wallbox_ac_dc_totals([_row(ac=11.2, dc=10.0), _row(ac=22.4, dc=20.0)])
    assert t["eff"] == 89.3


# ── one rule, not two ────────────────────────────────────────────────────────

def test_the_charge_card_no_longer_carries_its_own_copy_of_the_rule():
    """The card used to decide this itself (`_eff <= 100`). With the policy in the helper, a second
    copy is how the two screens drifted apart in the first place (#229)."""
    card = (ROOT / "web/templates/partials/charge_card.html").read_text()
    assert "_eff <= 100" not in card, "the card is still deciding this on its own"


def test_the_wallbox_page_shows_a_withheld_ratio_as_a_dash():
    """`kwh_pair` already renders `eff is none` as an em dash in the neutral colour — that is what
    a withheld ratio must reach, never a 130.4 % in the green bucket."""
    macro = (ROOT / "web/templates/partials/wallbox_sessions.html").read_text()
    assert "{% if eff is none %}text-slate-600" in macro
    assert "{% if eff is not none %}{{ eff | nice }}%{% else %}—{% endif %}" in macro
