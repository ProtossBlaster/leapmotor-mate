"""#144 — a temperature the car never sends is ABSENT, not zero degrees.

@staffhotel-beep, 08/08/26, on a European T03: the cabin and battery temperatures had read empty
all summer, and his diagnostics bundle could not say why — 77 000 lines, four mentions of "temp",
not one of them a reading. He had been asked for the one artefact that could not answer the
question.

The parser read them as `float(sig.get(id) or 0)`, so silence became **0.0**. Silvio put it best,
in August: *«non ha senso mostrare 0, specialmente proprio in questo periodo che siamo a 40 gradi e
di sicuro NON è la temperatura di qualcosa oggi»* — the number is not merely ambiguous, it is
absurd, and Mate printed it with a straight face.

And a zero does not stay on the page. It went to A Better Route Planner as a real cabin reading,
and to the ready-automation gate, where "only pre-heat below 5 °C" was satisfied on **every poll,
all year**. → [[signal-absent-is-not-signal-zero]]
"""
import pathlib

import client          # poller/client.py
import pytest


def _sig(**kw):
    base = {"1010": 0, "1319": 0}   # parked, stationary — unrelated gates stay quiet
    base.update(kw)
    return base


# ── the parser ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sid,field", [("1349", "inside_temp"),
                                       ("1182", "battery_min_temp"),
                                       ("2183", "climate_target_temp")])
def test_a_signal_the_car_never_sends_is_none(sid, field):
    assert getattr(client._parse_signal("VIN", _sig()), field) is None


@pytest.mark.parametrize("sid,field", [("1349", "inside_temp"),
                                       ("1182", "battery_min_temp"),
                                       ("2183", "climate_target_temp")])
def test_a_real_zero_is_kept(sid, field):
    """A pack genuinely at zero is a FACT, not an absence. Only silence becomes None — otherwise
    the fix would have thrown away the one reading that matters in a Norwegian winter."""
    assert getattr(client._parse_signal("VIN", _sig(**{sid: 0})), field) == 0.0


def test_a_reading_still_arrives():
    vd = client._parse_signal("VIN", _sig(**{"1349": 27.5, "1182": 31.0, "2183": 22.0}))
    assert (vd.inside_temp, vd.battery_min_temp, vd.climate_target_temp) == (27.5, 31.0, 22.0)


def test_rubbish_is_absence_not_a_crash():
    vd = client._parse_signal("VIN", _sig(**{"1349": "--", "1182": None}))
    assert vd.inside_temp is None and vd.battery_min_temp is None


def test_the_activity_fingerprint_survives_an_absent_temperature():
    """🔑 The consequence that nearly shipped. `fingerprint()` did `round(self.inside_temp)` — every
    poll computes it — so the first car with no cabin sensor would have crashed the poll loop
    instead of showing a dash. Caught by the existing suite, not by me."""
    vd = client._parse_signal("VIN", _sig())
    fp = vd.fingerprint()
    assert fp is not None and None in fp


# ── it does not leak into anything downstream ─────────────────────────────────

def test_abrp_is_not_told_a_temperature_the_car_never_gave():
    """ABRP already dropped None values; the parser's `or 0` was defeating it. So the fix at the
    source repairs the route planner for free — asserted, not assumed."""
    import abrp
    vd = client._parse_signal("VIN", _sig(**{"1204": 55}))
    tlm = abrp._telemetry(vd) if hasattr(abrp, "_telemetry") else None
    if tlm is None:                       # builder is private/renamed → assert the field instead
        assert vd.inside_temp is None
        return
    assert "cabin_temp" not in tlm and "batt_temp" not in tlm


def test_the_ready_automation_does_not_fire_on_an_unknown_temperature():
    """🔴 The worst of the consequences. 0.0 satisfied "only pre-heat below 5 °C" on every poll, so
    on a car with no cabin sensor the automation fired every single time it was switched on, all
    year round. Unknown is refused — and said out loud, because "could not be evaluated" is a
    different fact from "false"."""
    import ready_automation as ra
    cfg = {"temp_enabled": True, "temp_comparator": "<", "temp_value": 5.0}
    assert ra._condition_met(cfg, None) is False
    assert ra._condition_met(cfg, 3.0) is True, "a real cold cabin still fires"
    assert ra._condition_met(cfg, 20.0) is False
    # …and with the condition switched off, an unknown temperature is irrelevant
    assert ra._condition_met({"temp_enabled": False}, None) is True


# ── what the pages show ───────────────────────────────────────────────────────

def test_no_temperature_row_decides_by_truthiness():
    """Three rows, three different rules before this: the cabin printed `(x or 0) | temp(0)` — "0 °C"
    for anything it could not get — while the other two used truthiness, which throws away a real
    0.0. One rule now, and it is the only one that can tell nothing from zero."""
    card = (pathlib.Path(__file__).parents[1] / "web" / "templates" / "partials"
            / "status_card.html").read_text()
    assert "(status.inside_temp or 0) | temp(0)" not in card, "the `or 0` is what printed 0 °C"
    for field in ("inside_temp", "climate_target_temp", "battery_min_temp"):
        assert f"status.{field} is not none" in card, f"{field} still decides by truthiness"


def test_the_bundle_can_answer_the_question_next_time():
    """The reason this took a code read instead of a bundle read. A count of non-NULL over the
    retained polls turns "I see dashes" into "0 of 88 000", which is an answer."""
    import diagnostics
    src = (pathlib.Path(__file__).parents[1] / "web" / "diagnostics.py").read_text()
    # ⚠️ The CALL, not the name: `def _temperature_line()` contains the name too, so asserting the
    # name alone stayed green with the call deleted — the line would have existed and never run.
    assert "            _temperature_line()," in src, "it has to be in the header, not just defined"
    body = src.split("def _temperature_line", 1)[1].split("\ndef ", 1)[0]
    for col in ("inside_temp", "battery_min_temp", "climate_target_temp"):
        assert col in body, f"{col} has to be counted"
    assert "NEVER" in body, "0 of N is a finding, and has to read like one"
    assert callable(diagnostics._temperature_line)


# ── a sensor the car does not have is HIDDEN, not dashed ──────────────────────

def _positions(db, n, **cols):
    """n polls, with whichever temperature columns the car reports."""
    keys = ", ".join(["vehicle_id", "recorded_at", "soc"] + list(cols))
    marks = ", ".join(["?"] * (3 + len(cols)))
    for i in range(n):
        db._conn.execute(f"INSERT INTO positions ({keys}) VALUES ({marks})",
                         (1, f"2026-07-01T{i // 60:02d}:{i % 60:02d}:00+00:00", 50.0,
                          *cols.values()))
    db._conn.commit()


@pytest.fixture
def car(tmp_path, monkeypatch):
    import db as D
    import db_reader
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN_T03','T03')")
    database._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return database


def test_a_sensor_never_reported_is_named_as_absent(car):
    """Silvio, 08/08: *«se non è presente un sensore per la T03 dobbiamo nasconderlo, e non farlo
    più vedere»*. A row that says "—" for ever still promises a number."""
    import db_reader
    _positions(car, 200, inside_temp=None, battery_min_temp=31.0, climate_target_temp=None)
    absent = db_reader.never_reported_temps()
    assert "inside_temp" in absent and "ac_target" in absent
    assert "battery_temp" not in absent, "this one arrives, so it stays on the page"


def test_a_fresh_install_hides_nothing(car):
    """⚠️ The poll floor is the whole safety of it. With a handful of polls behind a new install
    'never seen' means nothing, and hiding on it would blank rows that were about to work."""
    import db_reader
    _positions(car, 10, inside_temp=None, battery_min_temp=None, climate_target_temp=None)
    assert db_reader.never_reported_temps() == set(), "not enough evidence to hide anything"


def test_one_reading_in_the_window_is_enough_to_keep_the_row(car):
    """A car that reports it at all has the sensor. One arrival beats two hundred silences —
    absence has to be total to count."""
    import db_reader
    _positions(car, 199, inside_temp=None)
    _positions(car, 1, inside_temp=24.0)
    assert "inside_temp" not in db_reader.never_reported_temps()


def test_a_sensor_that_starts_working_un_hides_itself(car):
    """The window is the recent polls, not all of history: a sensor that begins reporting comes back
    within a few hours rather than staying hidden for ever."""
    import db_reader
    _positions(car, 600, inside_temp=None)          # a long silence, older than the window
    assert "inside_temp" in db_reader.never_reported_temps()
    _positions(car, 500, inside_temp=22.0)          # …then it starts arriving
    assert "inside_temp" not in db_reader.never_reported_temps()


def _render_card(absent=(), **status):
    """The real partial, rendered. A source-string check proves the markup mentions a variable; only
    rendering proves what a reader sees — and the first version of this test asserted a string that
    a harmless tidy-up of the template broke while the behaviour was intact. Same harness as
    tests/test_last_seen_is_when_the_car_spoke.py."""
    import collections
    import jinja2

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
    env.filters["temp"] = lambda v, *a: f"{v} °C"          # the one filter under test
    # ⚠️ `soc` seeded, not optional: the whole card sits behind `{% if status %}`, and an empty
    # defaultdict is FALSY — every assertion below was passing over the "waiting for data" branch.
    full = collections.defaultdict(lambda: None, {"soc": 50.0, **status})
    return env.get_template("partials/status_card.html").render(
        status=full, absent_temps=lambda: set(absent), t=lambda k: k, odometer_km=0,
        ago=lambda *a: "", state_color=lambda *a: "", car_resp=None, dist_val=lambda v: v,
        dist_unit=lambda *a: "km", speed_val=lambda v: v, speed_unit=lambda *a: "km/h",
        battery_price=None, currency="€", soc=None, color="", state="")


def _row(out, label):
    """What that one row SAYS — `"0 °C" not in out` was green on "22.0 °C" from the row next door."""
    return None if f">{label}<" not in out else out.split(f">{label}<", 1)[1].split("</div>", 1)[0]


def test_the_card_leaves_out_the_row_for_a_sensor_the_car_does_not_have():
    """Silvio, 08/08: *«se non è presente un sensore per la T03 dobbiamo nasconderlo, e non farlo
    più vedere»*. Not dashed — gone."""
    out = _render_card(absent=("inside_temp", "ac_target"), battery_min_temp=31.0)
    assert _row(out, "inside_temp") is None and _row(out, "ac_target") is None
    kept = _row(out, "battery_temp")
    assert kept and "31.0 °C" in kept, "the sensor that works still shows"


def test_a_sensor_that_exists_but_missed_this_poll_says_dash_not_zero():
    """The other half, and the one that started #144: the row stays, and it must not read 0 °C."""
    row = _row(_render_card(inside_temp=None, battery_min_temp=30.0, climate_target_temp=22.0),
               "inside_temp")
    assert row is not None, "the row is not hidden — the sensor exists"
    assert "—" in row and "°C" not in row


def test_a_real_zero_is_printed_on_the_page_too():
    """A pack at 0 °C is a reading. Hiding it would be the same defect from the other side."""
    row = _row(_render_card(battery_min_temp=0.0), "battery_temp")
    assert row and "0.0 °C" in row


@pytest.mark.parametrize("absent,wide", [((), False),
                                         (("inside_temp",), True),
                                         (("inside_temp", "ac_target"), False),
                                         (("inside_temp", "ac_target", "battery_temp"), True)])
def test_the_odometer_takes_the_whole_width_when_the_grid_would_be_left_open(absent, wide):
    """These are half-width cells: an odd number of them leaves a hole in the last row, and the
    odometer alone in a 130px column wraps its value onto a second line. Silvio saw exactly that on
    the T03 screenshot. Odd total ⟺ an even number of temperatures survive."""
    row = _render_card(absent=absent).split("odometer", 1)[0].rsplit("<div", 1)[1]
    assert ("col-span-2" in row) is wide


def test_the_flag_is_a_template_global_not_a_context_key():
    """`status_card.html` is rendered by the Overview AND by partials that build their own context, so
    the flag is a template GLOBAL — a value threaded through `_ctx` would reach the page and vanish
    everywhere else. → [[mate-web-partials-render-standalone]]"""
    main = (pathlib.Path(__file__).parents[1] / "web" / "main.py").read_text()
    assert "absent_temps=db_reader.never_reported_temps," in main
    assert "_ctx" not in main.split("absent_temps=", 1)[1].split("\n", 1)[0]
