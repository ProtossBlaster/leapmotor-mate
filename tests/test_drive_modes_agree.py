"""The two halves of Mate must accept the same set of drive-mode tags (#185, @adoewa).

There is one list of drive modes, and there are two copies of it: the web validates what you pick
in Settings and what you tag a trip with, the poller validates the default it stamps on every NEW
trip. v2.13.0 added ECO and Custom to the web copy and left the poller's alone — so choosing ECO as
your default was accepted, stored, shown back to you selected, and then silently discarded by the
poller when the trip was born. Every screen agreed with you; the trips just came out untagged.

Nothing failed, because nothing compared the two lists. This does.

The two halves are separate import roots and cannot share a module (conftest puts both on the path
here, the container does not), so the duplication is structural — which is exactly why it needs a
test rather than a comment.
"""
import db as poller_db          # poller/db.py
import db_reader                # web/db_reader.py


def test_the_poller_accepts_every_mode_the_web_offers():
    """A mode the web stores but the poller rejects is #185: silently dropped on new trips."""
    assert set(poller_db._DRIVE_MODES) == set(db_reader.DRIVE_MODES)


def test_they_are_in_the_same_order():
    """Order is the order the screen shows them in; keeping it identical means the two copies can be
    diffed by eye when one of them changes."""
    assert tuple(poller_db._DRIVE_MODES) == tuple(db_reader.DRIVE_MODES)


def test_the_modes_added_in_v2_13_0_are_in_both():
    """Named explicitly: these two are the ones that were missing, and a future edit that drops
    them again should say so in the failure rather than just 'sets differ'."""
    for mode in ("eco", "custom"):
        assert mode in db_reader.DRIVE_MODES, f"{mode} missing from the web list"
        assert mode in poller_db._DRIVE_MODES, f"{mode} missing from the poller list"
