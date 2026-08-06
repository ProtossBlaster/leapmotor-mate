"""The poller's log line carries the three things that decide whether a charge opens.

@adoewa, #230, 06/08/26. His C10 charged from 49.8% to 90.0% over three and a quarter hours and
Mate opened nothing. The bundle he sent could prove the car was ONLINE the whole time — 185 polls,
185 distinct SoC values, frames two seconds old, against the 477 polls earlier the same night that
all carried ONE repeated frame aged up to nine hours — and it could prove the charge happened. It
could not say **why** no session opened, because the three inputs to that decision are nowhere in
the log:

    · the cable's own state   (signal 1149, normalised by `_is_plugged_in`)
    · the decision itself     (`_is_charging` → `charging_status`)
    · the pack current        (signal 1178)

They exist in `positions`, one row per poll, and in the diagnostics bundle as a SINGLE snapshot —
taken when the user pressed the button, which for him was ten hours after the cable came out. So
the one moment that mattered was the one moment nobody recorded in a readable form.

🔑 **Measured before building.** The same sweep over every bundle we hold — 7 bundles, 5 cars,
88 car-days — found 35 charges taken while parked, of which 34 were seen and **1** was lost: his.
2.9%, one car. So this is NOT a fix for a defect that bites everyone; it is the log saying enough
for the next one to be answerable at all, whoever it happens to. Silvio, 06/08: *«se dobbiamo
allargare il log lo facciamo per tutti così abbiamo anche più dati da analizzare»*.

Three fields, always present, ~20 bytes a line. Nothing new is collected: the poller already holds
all three on every poll and simply never wrote them down.
"""
import importlib.util
import pathlib
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 🔴 Loaded by PATH under a name of its own, never as a bare `import main`. `main.py` is one of the
# FIVE basenames shared by web/ and poller/, and conftest puts web/ first on sys.path — so a bare
# import returns the WEB's main, or whichever of the two some earlier test imported first. Run
# alone this file won its own race and passed; run in the suite it silently tested the wrong
# module. Six red tests, and the trap is documented precisely because it keeps being sprung.
_spec = importlib.util.spec_from_file_location("_poller_main_under_test", ROOT / "poller" / "main.py")
_poller_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_poller_main)


def _data(**kw):
    base = dict(plug_connected=False, charging_status=0, charge_current_a=0.0)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _fields(**kw):
    return _poller_main._charge_fields(_data(**kw))


# ── the three values, and they are readable ───────────────────────────────────

def test_it_says_the_cable_the_decision_and_the_current():
    out = _fields(plug_connected=True, charging_status=1, charge_current_a=-11.6)
    assert "plug=1" in out
    assert "chg=1" in out
    assert "-11.6" in out


def test_his_case_reads_as_three_zeroes():
    """What #230's line would have said for 202 polls: cable never declared, no decision, no
    current — while the SoC climbed 40 points. One line instead of half an hour of archaeology."""
    assert _fields() == "plug=0 chg=0 A=0.0"


def test_a_charge_in_progress():
    """And the control: the same three on a car that really is charging (Silvio's B10, 06/08)."""
    assert _fields(plug_connected=True, charging_status=1,
                   charge_current_a=-11.599) == "plug=1 chg=1 A=-11.6"


# ── the shapes that must not crash a poll ─────────────────────────────────────

def test_a_missing_current_is_not_a_crash():
    """A partial frame is normal — the line must still be written, or we lose the poll we most
    wanted to read."""
    assert _fields(charge_current_a=None) == "plug=0 chg=0 A=—"


def test_a_missing_plug_reads_as_unknown_not_as_false():
    """🔴 Absent is not zero — the rule this repo keeps relearning. `None` on the cable means the
    frame did not carry it, which is a different fact from 'the cable is out'."""
    assert "plug=?" in _fields(plug_connected=None)


def test_nothing_else_is_added():
    """Three fields, not five. The log line is written on every poll of every install; each byte
    is multiplied by ~2900 polls a day, per user, forever."""
    out = _fields(plug_connected=True, charging_status=1, charge_current_a=-11.6)
    assert out.count("=") == 3, out
    assert len(out) < 32, out


# ── and it reaches the line the bundle actually carries ───────────────────────

def test_the_poll_loop_logs_it():
    """A helper nobody calls is a helper. Anchored to the log call itself."""
    src = (ROOT / "poller" / "main.py").read_text()
    call = src.split('"SOC %.1f%%', 1)[1].split(")\n", 1)[0]
    assert "_charge_fields(data)" in call, "the poll loop does not log the charge fields"
