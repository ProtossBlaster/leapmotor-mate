"""One bad frame must not undo months of remembered hemisphere — #232.

@rop12770, #232, 06/08/26: a Portuguese C10 back in the sea west of Sardinia, on v3.8.5, with a
marker **22 seconds old**. Latitude right, longitude mirrored — the #30/#43/#158 signature. The
code that resolves the sign had not changed since v2.8.8 (`git log -S _resolve_coord`), so nothing
we shipped moved it.

Reading it again found the hole the comment above `_coord_sign` denies:

    # The memory is only ever written by a signed read, never by the fallback, so it can't
    # be polluted.

True only while the *signed* signal is actually signed. Signal 2 is believed whenever it is
non-zero — no sanity check at all. If the cloud emits the bare magnitude in that slot for a single
poll, Mate learns "east", the poller writes `gps_lon_sign = 1.0` into the database, and from that
moment **both** readers — the poller's fallback and the web's — mirror the car for good. The guard
built to survive a missing sign has no defence against a *wrong* one.

🔑 The physics the code was missing: **a car cannot teleport across the line.** Approaching the
meridian the longitude passes through zero, so a genuine crossing is always observed near it. A
dropped sign is observed at full magnitude — 8.6° W becoming 8.6° E is 1720 km in one poll.

Two numbers, chosen and declared rather than tuned:

- `_MERIDIAN_NEAR_DEG = 1.0` — within ~85 km of the line, a crossing is ordinary. Believe it at
  once, or a car driving Lisbon→Madrid would fight its own memory.
- `_SIGN_FLIP_CONFIRMATIONS = 10` — far from the line the flip has to be argued for, ten polls in
  a row (5 min parked, 100 s driving). A transient frame never gets there; a car genuinely shipped
  across the meridian with its SIM dark the whole way re-registers within minutes of coming back.
  Without this escape that owner would be mirrored forever with no way out.

⚠️ **The poller learns, the web only applies.** `db_reader` writes one position row on Refresh and
cannot count polls, so it refuses every far-from-the-line flip and follows the setting the poller
persists. Same threshold, no second opinion — the two must never disagree about where the car is.
"""
import logging

import client
import db as D
import db_reader
import pytest


# Real magnitudes: Porto (west of Greenwich) and Milan (east, the case that must not move).
PORTO = {"1": 1781076766374, "sts": 1781076766616,
         "2": -8.610000, "3": 41.150000,
         "3724": 8.610000, "3725": 41.150000,
         "100003": 62.6, "1204": 63, "1318": 2008, "1319": 0.0, "1010": 0}

# The same car, one poll later, with the sign missing from the SIGNED slot — what a dropped sign
# looks like from Mate's side: signal 2 has become indistinguishable from the unsigned magnitude.
PORTO_MIRRORED = dict(PORTO, **{"2": 8.610000})


def _lon(vin, sig):
    return client._parse_signal(vin, sig).longitude


@pytest.fixture(autouse=True)
def _clean_memory():
    """`_coord_sign` is module-level and outlives a test. Every test below owns its VIN, but the
    pending-flip counter is a second dict and forgetting it would leak a count into the next test."""
    yield
    client._coord_sign.clear()
    client._sign_flip_pending.clear()


# ── the refusal ───────────────────────────────────────────────────────────────

def test_one_mirrored_poll_does_not_move_the_car():
    """The whole point. Learn Porto, then hand it the mirrored frame."""
    _lon("VIN232A", PORTO)                         # learns lon = −
    assert _lon("VIN232A", PORTO_MIRRORED) == -8.61


def test_and_it_does_not_learn_the_wrong_sign_either():
    """🔴 The lasting damage is not the row, it is the memory: poller/main.py persists whatever
    `get_coord_signs` returns, so a flip here reaches the database and outlives the restart."""
    _lon("VIN232B", PORTO)
    _lon("VIN232B", PORTO_MIRRORED)
    assert client.get_coord_signs("VIN232B")["lon"] == -1.0


def test_the_refusal_is_written_in_the_log(caplog):
    """Silent correctness is untriageable: the poller log is in the bundle, and this line is how
    the next #232 gets answered without asking anyone to run anything."""
    _lon("VIN232C", PORTO)
    with caplog.at_level(logging.WARNING, logger="client"):
        _lon("VIN232C", PORTO_MIRRORED)
    assert any("sign" in r.message.lower() for r in caplog.records), \
        "a refused sign flip must say so"


# ── and the crossings it must NOT refuse ──────────────────────────────────────

def test_a_car_crossing_the_meridian_is_believed_at_once():
    """Lisbon→Madrid really does change hemisphere. Near the line the sign flips with no argument,
    or the guard becomes the bug."""
    _lon("VIN232D", PORTO)
    near = dict(PORTO, **{"2": 0.120000, "3724": 0.120000})
    assert _lon("VIN232D", near) == 0.12
    assert client.get_coord_signs("VIN232D")["lon"] == 1.0


def test_the_first_signed_poll_still_teaches_the_sign():
    """A fresh install has no memory to protect — the very first signed read is authoritative at
    any magnitude, exactly as before. Requiring confirmations here would put every new west-of-
    Greenwich owner in the sea for ten polls."""
    assert _lon("VIN232E", PORTO) == -8.61
    assert client.get_coord_signs("VIN232E")["lon"] == -1.0


def test_a_car_that_really_moved_hemisphere_wins_in_the_end():
    """The escape hatch: shipped abroad with the SIM dark, so the crossing was never observed near
    the line. Ten agreeing polls override the memory."""
    _lon("VIN232F", PORTO)
    for _ in range(client._SIGN_FLIP_CONFIRMATIONS - 1):
        assert _lon("VIN232F", PORTO_MIRRORED) == -8.61      # still refused
    assert _lon("VIN232F", PORTO_MIRRORED) == 8.61           # argued for long enough
    assert client.get_coord_signs("VIN232F")["lon"] == 1.0


def test_one_good_frame_in_between_resets_the_argument():
    """🔑 A cloud that flickers must never accumulate. Nine bad frames and one good one leaves the
    car exactly where it was — otherwise a long enough drive collects ten glitches by chance."""
    _lon("VIN232G", PORTO)
    for _ in range(client._SIGN_FLIP_CONFIRMATIONS - 1):
        _lon("VIN232G", PORTO_MIRRORED)
    _lon("VIN232G", PORTO)                                   # the car reports itself west again
    assert _lon("VIN232G", PORTO_MIRRORED) == -8.61          # count restarted, not resumed


def test_a_negative_reading_is_never_second_guessed():
    """🔑 The guard is one-directional, and it has to be. A dropped sign can only ever produce a
    POSITIVE value — the unsigned signals are magnitudes, they have no minus to lose. So a negative
    reading is proof the slot really is signed, and doubting it would only delay a car that moved
    into the western hemisphere for no reason at all."""
    milan = {"2": 9.124942, "3": 45.443407, "3724": 9.124942, "3725": 45.443407,
             "1010": 0, "1319": 0.0, "100003": 100.0}
    _lon("VIN232K", milan)                                   # learns lon = +
    moved = dict(milan, **{"2": -8.610000, "3724": 8.610000})
    assert _lon("VIN232K", moved) == -8.61
    assert client.get_coord_signs("VIN232K")["lon"] == -1.0


# ── the equator gets the same guard ───────────────────────────────────────────

def test_the_southern_hemisphere_is_guarded_too():
    """Latitude runs through the same resolver — a Sydney car must not surface in Siberia because
    one frame forgot the minus."""
    sydney = {"2": 151.2093, "3": -33.8688, "3724": 151.2093, "3725": 33.8688,
              "1010": 0, "1319": 0.0, "100003": 52.6}
    client._parse_signal("VIN232H", sydney)
    flat = dict(sydney, **{"3": 33.8688})
    assert client._parse_signal("VIN232H", flat).latitude == -33.8688


# ── nothing changes for the cars that were always fine ────────────────────────

def test_an_eastern_car_never_meets_the_guard():
    """Milan: signed and unsigned agree, no flip is ever proposed. The guard must be invisible."""
    milan = {"2": 9.124942, "3": 45.443407, "3724": 9.124942, "3725": 45.443407,
             "1010": 0, "1319": 0.0, "100003": 100.0}
    for _ in range(3):
        assert _lon("VIN232I", milan) == 9.124942
    assert client.get_coord_signs("VIN232I")["lon"] == 1.0


def test_a_poll_with_no_signed_pair_is_untouched_by_this():
    """#43's fallback still owns that path: no signed value means nothing to argue about, the
    remembered sign is simply applied to the magnitude."""
    _lon("VIN232J", PORTO)
    unsigned_only = {k: v for k, v in PORTO.items() if k not in ("2", "3")}
    assert _lon("VIN232J", unsigned_only) == -8.61


# ── the web writes what the poller decided, and never the other way round ─────

def _web_db(tmp_path, monkeypatch, *, lon_sign, name):
    path = str(tmp_path / f"{name}.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN','B10')")
    db._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('gps_lon_sign',?)",
                     (str(lon_sign),))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _stored_lon(path):
    import sqlite3
    con = sqlite3.connect(path)
    lon = con.execute("SELECT longitude FROM positions ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    return lon


def test_the_refresh_button_does_not_file_a_mirrored_row(tmp_path, monkeypatch):
    """The web keeps its own copy of this parse (#158) and believed signal 2 just as blindly. One
    Refresh on a bad frame put the NEWEST row — the one the Overview map draws — out at sea."""
    path = _web_db(tmp_path, monkeypatch, lon_sign=-1.0, name="w1")
    db_reader.save_fresh_signals({"2": 8.610000, "3": 41.15, "3724": 8.610000, "3725": 41.15,
                                  "1010": 0, "1319": 0.0, "100003": 52.6})
    assert _stored_lon(path) == -8.61


def test_the_web_still_believes_a_crossing_near_the_line(tmp_path, monkeypatch):
    path = _web_db(tmp_path, monkeypatch, lon_sign=-1.0, name="w2")
    db_reader.save_fresh_signals({"2": 0.120000, "3": 41.15, "3724": 0.120000, "3725": 41.15,
                                  "1010": 0, "1319": 0.0, "100003": 52.6})
    assert _stored_lon(path) == 0.12


def test_the_web_lets_a_negative_reading_through(tmp_path, monkeypatch):
    """Same one-directional rule on this side, or the Refresh button would drag an eastern car
    back east every time it reported itself west — and fight the poller doing it."""
    path = _web_db(tmp_path, monkeypatch, lon_sign=1.0, name="w4")
    db_reader.save_fresh_signals({"2": -8.610000, "3": 41.15, "3724": 8.610000, "3725": 41.15,
                                  "1010": 0, "1319": 0.0, "100003": 52.6})
    assert _stored_lon(path) == -8.61


def test_the_two_processes_agree_on_what_near_the_line_means():
    """⚠️ The threshold lives twice — `poller/client` and `web/db_reader` are separate sys.path
    roots and cannot import each other. Two values would mean the Refresh button and the poller
    disagreeing about the same car within one degree of the meridian."""
    assert db_reader._MERIDIAN_NEAR_DEG == client._MERIDIAN_NEAR_DEG


def test_the_web_never_rewrites_the_remembered_sign(tmp_path, monkeypatch):
    """⚠️ Two processes, one setting. If the web learned as well, a Refresh during a glitch would
    overwrite what the poller is still arguing about — and the poller's ten-poll guard would be
    decided by a button."""
    _web_db(tmp_path, monkeypatch, lon_sign=-1.0, name="w3")
    db_reader.save_fresh_signals({"2": 8.610000, "3": 41.15, "3724": 8.610000, "3725": 41.15,
                                  "1010": 0, "1319": 0.0, "100003": 52.6})
    assert db_reader.get_setting("gps_lon_sign") == "-1.0"
