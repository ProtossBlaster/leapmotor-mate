"""The remembered hemisphere is re-derived from the car's own positions — #232, @rop12770.

The v3.8.6 guard stopped Mate LEARNING a dropped sign. It could not un-learn one already stored,
and rop12770's bundle showed exactly that install: eighteen trip starts logged at longitude −7.2
between 1 and 5 August, and `gps_lon_sign = 1.0`. One frame after the last of those trips arrived
with its minus missing, the pre-guard code believed it, and the poisoned value became what every
restart primed from. Two weeks of evidence, beaten by one frame — and the guard shipped a day late
for him.

🔑 The fix is not another guard and not a button: the answer was already on his disk. A car cannot
have driven a fortnight of kilometres in a place it has never been, so the position history
outvotes any single frame by construction, and re-deriving the sign at startup means installing
the update IS the repair.

⚠️ Two ideas were measured and thrown away before this one, both recorded so they don't come back:

- **"forget the sign"** — with the memory cleared `known is None`, and `_resolve_coord` takes its
  first branch: it re-learns `+1` from the next positive frame and is back in the sea within one
  poll.
- **"ask the owner which hemisphere"** — Silvio: *«l'utente potrebbe dirti qualsiasi cosa»*. And
  unnecessary: we hold better evidence than the owner's memory.

The land/sea test was measured and rejected too: Open-Meteo puts 40.2 N / 8.6 E at **537 m** — that
is Sardinia, not sea. The mirror of western Europe is eastern Europe, all of it dry.
"""
import importlib.util
import logging
import pathlib

import db as D
import diagnostics
import pytest


def _poller_main():
    """Load poller/main.py under its own name — a bare `import main` gets web/main.py, and the
    collision is silent: it imports fine and simply has no reconcile_coord_signs."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_signs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


poller_main = _poller_main()


def _db(tmp_path, name="h.db"):
    database = D.Database(str(tmp_path / name))
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN232','C10')")
    database._conn.commit()
    return database


def _places(database, coords, *, repeat=1):
    """Insert `repeat` rows for each (lat, lon). Rows, not places — the distinction under test."""
    rows = [(1, f"2026-08-0{1 + (i % 5)}T10:00:00", lat, lon)
            for lat, lon in coords for i in range(repeat)]
    database._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude) VALUES (?,?,?,?)",
        rows)
    database._conn.commit()


# Portugal: latitude right, longitude negative. 30 distinct places is an ordinary few days.
WEST = [(40.5 + i / 100, -7.2 - i / 100) for i in range(30)]


# ── what the history says ─────────────────────────────────────────────────────

def test_the_history_names_the_hemisphere(tmp_path):
    database = _db(tmp_path)
    _places(database, WEST)
    assert database.dominant_coord_signs(1) == {"lat": 1.0, "lon": -1.0}


def test_a_frozen_frame_is_one_vote_not_two_thousand(tmp_path):
    """🔑 The test the whole design turns on.

    While the car sleeps the cloud re-serves one frozen frame, and `Recorder.process` still calls
    save_position() every 30 s — only DRIVING skips repeats. rop12770 banked ~1900 identical rows
    over sixteen hours, carrying the poisoned sign. Counted as ROWS, one stuck frame outvotes real
    driving 60:1 and the repair would confirm the bug it exists to undo. Counted as PLACES, it is
    worth one vote, which is exactly what it is.

    Seen red against `SELECT latitude, longitude FROM positions` (no DISTINCT): +1.0.
    """
    database = _db(tmp_path)
    _places(database, WEST)                       # 30 places the car really drove through
    _places(database, [(40.5, 7.2)], repeat=2000)  # one mirrored place, re-served all night
    assert database.dominant_coord_signs(1)["lon"] == -1.0


def test_the_southern_hemisphere_is_answered_too(tmp_path):
    """Latitude is the axis nobody hits in Europe, so it is the one that rots untested."""
    database = _db(tmp_path)
    _places(database, [(-33.9 - i / 100, 18.4 + i / 100) for i in range(30)])
    assert database.dominant_coord_signs(1) == {"lat": -1.0, "lon": 1.0}


# ── and when it must keep quiet ───────────────────────────────────────────────

def test_a_long_gps_outage_does_not_crowd_the_real_history_out(tmp_path):
    """A missing coordinate is the axis saying nothing, not a position off the coast of Africa.

    ⚠️ The first version of this test used 5000 rows of (0, 0) and could not fail: DISTINCT already
    collapses them to ONE place, and the counting below ignores zeros anyway. It asserted a property
    guaranteed twice over by other code — exactly the green-that-cannot-go-red this file is meant to
    avoid. What actually bites is a long outage with a LIVE latitude and a dead longitude: those are
    400 distinct places, and being the most recent they take every slot in the sample window and
    push the real driving out of it.

    Seen red with the `AND latitude != 0 AND longitude != 0` filter removed: {} instead of west.
    """
    database = _db(tmp_path)
    _places(database, WEST)                                            # the real history, older
    _places(database, [(40.5 + i / 100, 0.0) for i in range(400)])     # then GPS drops out
    assert database.dominant_coord_signs(1)["lon"] == -1.0


def test_too_little_history_says_nothing(tmp_path):
    """A fresh install must not have its sign decided by its first five rows."""
    database = _db(tmp_path)
    _places(database, WEST[:5])
    assert database.dominant_coord_signs(1) == {}


def test_a_split_history_says_nothing(tmp_path):
    """A car that genuinely moved across the line has no majority, and inventing one would strand
    the owner the guard's ten-poll escape exists to rescue. Silence hands it back to the caller."""
    database = _db(tmp_path)
    _places(database, WEST)
    _places(database, [(40.5 + i / 100, 7.2 + i / 100) for i in range(30)])
    assert "lon" not in database.dominant_coord_signs(1)


# ── the startup reconciliation ────────────────────────────────────────────────

def _stored(database, axis):
    return database.get_setting(f"gps_{axis}_sign", "")


def test_a_poisoned_setting_is_corrected_from_the_history(tmp_path):
    """rop12770's install, reproduced: negative history, positive setting."""
    database = _db(tmp_path)
    _places(database, WEST)
    database.set_setting("gps_lon_sign", "1.0")
    poller_main.reconcile_coord_signs(database, 1, "VIN232")
    assert float(_stored(database, "lon")) == -1.0


def test_the_correction_reaches_the_setting_the_web_reads(tmp_path):
    """⚠️ The web has no history of its own — it applies this setting and nothing else. Correcting
    only the poller's memory would fix the trips and leave the marker at sea, which is precisely the
    split rop12770 reported."""
    database = _db(tmp_path)
    _places(database, WEST)
    database.set_setting("gps_lon_sign", "1.0")
    poller_main.reconcile_coord_signs(database, 1, "VIN232")
    reopened = D.Database(str(tmp_path / "h.db"))
    assert float(reopened.get_setting("gps_lon_sign", "0")) == -1.0


def test_a_setting_the_history_agrees_with_is_left_alone(tmp_path, caplog):
    """No churn and no noise on the overwhelmingly common case: everybody east of Greenwich."""
    database = _db(tmp_path)
    _places(database, [(45.4 + i / 100, 9.1 + i / 100) for i in range(30)])   # Milan
    database.set_setting("gps_lon_sign", "1.0")
    with caplog.at_level(logging.WARNING, logger="main"):
        poller_main.reconcile_coord_signs(database, 1, "VIN232")
    assert float(_stored(database, "lon")) == 1.0
    assert not [r for r in caplog.records if "disagrees" in r.message]


def test_the_correction_is_written_in_the_log(tmp_path, caplog):
    """The poller log ships inside the bundle: this line is how the next #232 gets answered without
    asking anyone to run anything. It must carry no coordinate."""
    database = _db(tmp_path)
    _places(database, WEST)
    database.set_setting("gps_lon_sign", "1.0")
    with caplog.at_level(logging.WARNING, logger="main"):
        poller_main.reconcile_coord_signs(database, 1, "VIN232")
    said = [r.getMessage() for r in caplog.records if "disagrees" in r.getMessage()]
    assert said, "a corrected sign must say so"
    assert "7.2" not in said[0] and "40.5" not in said[0], "the log must not carry a position"


def test_an_unreadable_history_does_not_stop_the_poller(tmp_path, monkeypatch, caplog):
    """A sign we cannot re-derive is a worse map, not a dead poller."""
    database = _db(tmp_path)
    database.set_setting("gps_lon_sign", "-1.0")
    monkeypatch.setattr(D.Database, "dominant_coord_signs",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such table")))
    with caplog.at_level(logging.WARNING, logger="main"):
        poller_main.reconcile_coord_signs(database, 1, "VIN232")
    assert float(_stored(database, "lon")) == -1.0


# ── the bundle line that sent me the wrong way ────────────────────────────────

def _shape_line(monkeypatch, signals, lon_sign="1.0"):
    monkeypatch.setattr(diagnostics.db_reader, "get_setting",
                        lambda k, d=None: lon_sign if k == "gps_lon_sign" else "1.0")
    return diagnostics._gps_shape_line(signals)


def test_a_signal_that_arrives_as_zero_is_not_called_present(monkeypatch):
    """🔴 The old line tested `not in (None, "")`, so a zero counted as present and it read
    `signals present : 2, 3, …`. I read that as "the signed pair arrives" and spent two hours on a
    diagnosis built on it. A zero is the axis saying nothing; it must not look like a coordinate.

    Seen red against the old presence-only line.
    """
    line = _shape_line(monkeypatch, {"2": 0, "3": 41.1, "3724": 8.6})
    assert "2:zero" in line


def test_the_line_shows_the_sign_of_the_signed_pair(monkeypatch):
    """One character per axis: enough to answer the question, no magnitude, nothing to locate
    anyone with — the same quadrant the remembered sign below it already gives away."""
    line = _shape_line(monkeypatch, {"2": -8.61, "3": 41.15, "3724": 8.61, "3725": 41.15})
    assert "2:−" in line and "3:+" in line and "3724:+" in line


def test_an_absent_signal_is_still_absent(monkeypatch):
    """The one thing the old line did get right must survive the rewrite."""
    line = _shape_line(monkeypatch, {"3724": 8.61})
    assert "2:" not in line and "3724:+" in line


@pytest.mark.parametrize("bad", ["", None])
def test_an_empty_signal_is_not_reported_at_all(monkeypatch, bad):
    assert "2:" not in _shape_line(monkeypatch, {"2": bad, "3724": 8.61})
