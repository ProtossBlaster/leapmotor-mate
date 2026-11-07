"""A car keeps the side of the line a frame placed it on until its odometer moves on — #458.

#458: a car parked overnight, the poller restarted, and at dawn the car woke and sent fresh frames
with the minus dropped from its longitude. Ten in a row is what the rule asks of a car shipped
across the line, so Mate moved it to the other hemisphere. They were not ten witnesses of a move:
the odometer had not changed.

The first new frame at an odometer reading whose signed coordinates are believed as sent is the
anchor. While the odometer shows that reading, a frame that lands within PARKED_RADIUS_KM of the
anchor on the anchor's sides gets its minus back, also near the line, and counts towards no flip.
A negative reading is not doubted, as before: a lost sign cannot add a minus. A frame without an
odometer reading is at the last one seen. The anchor is built only from coordinates as the frame
sent them, never from a position the rule corrected or a frame that ends an argument, and not at
the reading where the sign last changed. The poller persists it and the web places its rows by it, so the two write the same row
for the same frame.
"""
import logging
import math

import client
import db_reader
import gps_position
import poll_cycle_fixture as F
import pytest

PM = F.poller_main("poller_main_gps_anchor")

PORTO = (41.15, -8.61)
MILAN = (45.443407, 9.124942)
LONDON = (51.5, -0.12)
BUENOS_AIRES = (-34.6, -58.4)
K = 2008
_CLOCK = [int(F.NOW * 1000) - 86_400_000]


def fr(lat, lon, km: "int | None" = K, *, same_frame=False):
    """A frame the car sends: signed coordinates, their magnitudes, an odometer reading (None =
    absent) and a clock 15 s on from the previous frame."""
    if not same_frame:
        _CLOCK[0] += 15_000
    frame = {"1": _CLOCK[0], "2": lon, "3": lat, "3724": abs(lon), "3725": abs(lat),
             "1010": 0, "1319": 0.0, "100003": 60.0}
    return frame | ({"1318": km} if km else {})


def place(sig, vin=F.VIN):
    data = client._parse_signal(vin, sig)
    return data.latitude, data.longitude


@pytest.fixture(autouse=True)
def _clean_memory():
    client._gps.clear()
    yield
    client._gps.clear()


@pytest.fixture
def loop(tmp_path, monkeypatch):
    """The real poll loop on the real parser, with the web and the Overview reading its database."""
    run = F.make_poll(PM, tmp_path, monkeypatch)
    monkeypatch.setattr(db_reader, "DB_PATH", run.db._path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: run.vid)

    def poll(sig):
        run(client._parse_signal(F.VIN, sig), advance=15)
        return tuple(run.db._conn.execute(
            "SELECT latitude, longitude FROM positions ORDER BY id DESC LIMIT 1").fetchone())

    def restart():
        client._gps.clear()
        PM.reconcile_coord_signs(run.db, run.vid, F.VIN)

    return poll, restart


def _overview():
    status = db_reader.get_latest_status()
    return status["latitude"], status["longitude"]


# ── the reports ────────────────────────────────────────────────────────────────

def test_458_a_parked_car_is_not_moved_by_frames_without_their_minus(loop):
    poll, restart = loop
    poll(fr(*PORTO))
    poll(fr(*PORTO))
    restart()                                               # the poller restarts overnight
    poll(fr(*PORTO, same_frame=True))                       # the cloud re-serves the last frame
    for _ in range(3 * gps_position.FLIP_CONFIRMATIONS):    # dawn: fresh frames, minus dropped
        assert poll(fr(PORTO[0], -PORTO[1])) == PORTO
    assert client.get_coord_signs(F.VIN)["lon"] == -1.0
    assert _overview() == PORTO
    db_reader.save_fresh_signals(fr(PORTO[0], -PORTO[1]), F.VIN)    # a Refresh on the same
    assert _overview() == PORTO


@pytest.mark.parametrize("where", [PORTO, BUENOS_AIRES], ids=["longitude", "both"])
def test_a_first_reading_without_its_minus_does_not_hold_the_car_there(loop, where):
    """With nothing learned yet the first frame is believed as sent, and it anchors the car. The
    minus the next frames carry is still believed, after a restart and on the web's row too."""
    poll, restart = loop
    dropped = tuple(abs(v) for v in where)
    assert poll(fr(*dropped)) == dropped
    shown = [poll(fr(*where)) for _ in range(3)]
    restart()
    shown += [poll(fr(*where)) for _ in range(3)]
    db_reader.save_fresh_signals(fr(*where), F.VIN)
    assert (set(shown), _overview()) == ({where}, where)


def test_an_added_minus_is_believed_and_argued_back_as_before(loop):
    """A minus the car does not have is believed at once, as any negative reading always was. Ten
    good frames bring the car back, also across the next kilometre: the anchor does not hold it on
    the wrong side meanwhile."""
    poll, _ = loop
    added = (MILAN[0], -MILAN[1])
    shown = [poll(fr(*MILAN)), poll(fr(*added)), poll(fr(*added))]
    shown += [poll(fr(*MILAN)) for _ in range(4)]
    shown += [poll(fr(*MILAN, K + 1)) for _ in range(6)]
    assert shown == [MILAN] + [added] * 11 + [MILAN]


def test_near_the_line_too():
    """London sits 0.12° west of Greenwich, inside the band where a frame across the line is
    believed at once: that band is what let a dropped minus through for a car parked there."""
    place(fr(*LONDON))
    assert place(fr(LONDON[0], -LONDON[1])) == LONDON


# ── the thresholds ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("km_away,held", [(1.9, True), (2.1, False)])
def test_the_anchor_holds_within_its_radius(km_away, held):
    """Beyond the radius the frame is left to the band, which believes it."""
    place(fr(*LONDON))
    lat = LONDON[0] + math.degrees(km_away / 6371.0)
    assert place(fr(lat, -LONDON[1]))[1] == (LONDON[1] if held else -LONDON[1])


@pytest.mark.parametrize("lon,shown", [(0.99, 0.99), (1.01, -1.01)])
def test_a_moving_car_is_believed_across_the_line_only_near_it(lon, shown):
    """The anchor leaves a car that drove on to the rule before it: near the line, believe."""
    place(fr(LONDON[0], -3.0, K))
    assert place(fr(LONDON[0], lon, K + 40))[1] == shown


def test_a_crossing_within_the_anchors_kilometre_waits_for_the_next_one():
    """The trade-off of the radius: a car that crosses the line before the odometer moves on is
    shown on the side it started from, under two kilometres off, until the next reading."""
    place(fr(LONDON[0], -0.003, K))
    assert place(fr(LONDON[0], 0.004, K))[1] == -0.004
    assert place(fr(LONDON[0], 0.012, K + 1))[1] == 0.012


# ── what can never confirm a wrong sign ────────────────────────────────────────

def test_a_frame_that_changed_the_sign_is_not_anchored_by_the_next_one():
    """An added minus while driving is believed, as a negative reading always was. The next one at
    the same reading must not become the anchor, or the good frames after it would be held on the
    wrong side until the car drove on."""
    place(fr(*MILAN, K - 1))
    place(fr(MILAN[0], -MILAN[1], K))
    place(fr(MILAN[0], -MILAN[1], K))
    for _ in range(gps_position.FLIP_CONFIRMATIONS - 1):
        assert place(fr(*MILAN, K))[1] == -MILAN[1]         # argued, as before
    assert place(fr(*MILAN, K)) == MILAN


def test_a_position_the_rule_corrected_never_becomes_the_anchor():
    """With the wrong sign remembered, a good frame is shown mirrored. Anchored there, it would hold
    every good frame after it on the mirror."""
    place(fr(*MILAN, K - 1))
    place(fr(MILAN[0], -MILAN[1], K))                       # the wrong sign, learned
    for _ in range(gps_position.FLIP_CONFIRMATIONS - 1):
        assert place(fr(*MILAN, K + 1))[1] == -MILAN[1]
    assert place(fr(*MILAN, K + 1)) == MILAN


@pytest.mark.parametrize("restarted", [False, True])
def test_a_frame_that_ends_an_argument_never_becomes_the_anchor(loop, restarted):
    """An added minus is believed and the good frames after it are argued. A second one at the next
    reading ends that argument by agreeing with the wrong sign: anchored, it would hold every good
    frame after it on the wrong side for as long as the car stood there. A restart in between
    leaves the argument open."""
    poll, restart = loop
    added = (MILAN[0], -MILAN[1])
    poll(fr(*MILAN, K - 1))
    poll(fr(*added, K))
    for km in (K, K, K, K + 1, K + 1):
        poll(fr(*MILAN, km))
    if restarted:
        restart()
    poll(fr(*added, K + 1))
    shown = [poll(fr(*MILAN, K + 1)) for _ in range(gps_position.FLIP_CONFIRMATIONS)]
    assert shown == [added] * (gps_position.FLIP_CONFIRMATIONS - 1) + [MILAN]


def test_an_anchor_the_history_overruled_places_nothing():
    """At startup the history may correct a sign (#232); an anchor kept from before would speak for
    the side it corrected."""
    client.seed_coord_signs(F.VIN, 1.0, -1.0)
    client.restore_gps_record(F.VIN, '{"anchor": [45.443407, 9.124942, 2008], "learned_at_km": null}')
    assert place(fr(*MILAN))[1] == -MILAN[1]


# ── frames the anchor has to place ─────────────────────────────────────────────

def test_a_frame_without_an_odometer_reading_is_where_the_car_was():
    place(fr(*LONDON))
    assert place(fr(LONDON[0], -LONDON[1], None)) == LONDON
    assert client.get_coord_signs(F.VIN)["lon"] == -1.0


@pytest.mark.parametrize("restarted", [False, True])
def test_a_frame_without_a_fix_still_says_the_car_drove_on(loop, restarted):
    """A frame that lost its latitude still reports the next kilometre; a late frame after it does
    not take the reading back. The next frame without an odometer is past the anchor's reading,
    and near the line it is believed."""
    poll, restart = loop
    poll(fr(LONDON[0], -0.003, K))
    no_fix = fr(LONDON[0], 0.004, K + 1)
    del no_fix["3"], no_fix["3725"]
    poll(no_fix)
    poll(dict(fr(LONDON[0], -0.003, K), **{"1": _CLOCK[0] - 60_000}))
    if restarted:
        restart()
    assert poll(fr(LONDON[0], 0.012, None)) == (LONDON[0], 0.012)


def test_a_jump_at_the_anchors_reading_does_not_replace_it():
    """The anchor is the first frame at a reading. A fix that strays 11 km is shown as it came, and
    the car is still held where it stands."""
    place(fr(*LONDON))
    place(fr(LONDON[0] + 0.1, LONDON[1]))
    assert place(fr(LONDON[0], -LONDON[1])) == LONDON


def test_a_frame_the_anchor_places_breaks_a_run():
    """Nine frames 2.2 km off, beyond the anchor's reach, are argued; a good one at the anchor in
    between starts the argument over, as a good frame always did."""
    place(fr(*PORTO))
    off = (PORTO[0] + 0.02, -PORTO[1])
    for _ in range(gps_position.FLIP_CONFIRMATIONS - 1):
        assert place(fr(*off)) == (off[0], PORTO[1])
    place(fr(*PORTO))
    assert place(fr(*off)) == (off[0], PORTO[1])


def test_a_car_that_drove_away_is_anchored_again():
    place(fr(*PORTO, K))
    place(fr(41.16, -8.62, K + 1))
    assert place(fr(41.16, 8.62, K + 1)) == (41.16, -8.62)


def test_the_overruled_frames_are_logged_without_a_place(caplog):
    """The poller log goes into the public diagnostics bundle: a line per frame would fill it on a
    car awake for hours, and no line would leave the next #458 unexplained."""
    place(fr(*PORTO))
    with caplog.at_level(logging.WARNING, logger="gps_position"):
        for _ in range(250):
            place(fr(PORTO[0], -PORTO[1]))
    said = [r.getMessage() for r in caplog.records]
    assert [m.split("(")[-1] for m in said] == [f"frames in a row: {n})" for n in (1, 100, 200)]
    assert not any("8.61" in m or "41.15" in m for m in said)


# ── the poller and the web write the same row ──────────────────────────────────

SEQUENCES = {
    "parked-near-the-line": [fr(*LONDON), fr(LONDON[0], -LONDON[1]), fr(LONDON[0], -LONDON[1], None),
                             fr(*LONDON)],
    "an-argument-while-driving": [fr(*PORTO), fr(41.2, 8.65, K + 1), fr(41.25, 8.7, K + 2),
                                  fr(41.3, -8.75, K + 3), fr(41.3, 8.75, K + 3)],
    "a-crossing": [fr(51.5, -0.3, K), fr(51.5, -0.1, K + 2), fr(51.5, 0.05, K + 4),
                   fr(51.5, 0.2, K + 6), fr(51.5, -0.2, K + 6)],
}


@pytest.mark.parametrize("frames", SEQUENCES.values(), ids=SEQUENCES.keys())
def test_the_web_writes_the_row_the_poller_wrote(loop, frames):
    poll, _ = loop
    for frame in frames:
        polled = poll(frame)
        db_reader.save_fresh_signals(frame, F.VIN)
        assert _overview() == polled
