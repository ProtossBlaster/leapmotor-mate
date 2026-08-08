"""The COSTS PER 100KM card also says how many kWh those 100 km took — by energy BALANCE.

@michapr, BetaTester #25, 06/08/26, with the arithmetic worked out on his own 47-trip / 15-charge
history and a patch attached. He also says, before anyone else could, why the obvious answer is the
wrong one:

    «`reev_total_consumption()`'s `kwh_100km` looks like the obvious answer, but it's trip-only by
    construction — it sums `(start_soc - end_soc)` per trip. […] it would still miss every kWh that
    left the pack outside of a trip.»

Which is Silvio's own rule for this card, arrived at independently — *«se deve essere un costo deve
essere totale non parziale»*, 05/08. Measured for #207, only **71.8%** of a bill lands on trips at
all; the rest leaves the battery standing still. So the kWh figure is built the same way the € one
is: treat the whole window as ONE system and never attribute anything trip by trip.

    consumed = (energy charged inside the window) − (net change in stored energy across it)

🔑 **Why the same formula is right on both cars, which is the part worth keeping.** Energy enters
the pack from two places, the grid and — on a range-extender — the generator:

    Δstored = charged + generator − consumed      →      consumed = charged + generator − Δstored

`charged − Δstored` therefore returns what left the pack MINUS the generator's contribution: the
grid-derived half, and nothing else. On a card that prices fuel separately that is exactly the
number wanted — the same refusal to bill the tank twice that `reev_total_consumption` already
documents. On a BEV `generator` is zero and it degenerates to plain consumption. One formula, two
cars, no `is_reev` branch.

⚠️ The estimate enters only through the SMALL term. `Δstored` is SoC × nominal capacity, and an
LFP's SoC is counted rather than measured (drift ±15%). On his window that term is 3.80 kWh against
63.89 charged — **6%** — so a 15% error on it moves the answer by under 1%. That is the strength of
the balance, and it is also its limit: a window that ends much fuller or emptier than it started
leans on it harder.

He cross-checked it two independent ways on his own history — bottom-up per-trip `ec_kwh` plus every
unexplained SoC gap gave 12.57, this top-down balance 12.55. 0.02 apart.
"""
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','B10')")
    database.set_setting("battery_capacity_kwh", "50.0")
    database._conn.commit()
    return database


def _trip(db, *, start, end, km, soc):
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (1,?,?,?,?,?)", (start, end, km, soc[0], soc[1]))
    db._conn.commit()


def _charge(db, *, start, end, kwh, cost=1.0):
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, cost) VALUES (1,?,?,20,60,?,?)", (start, end, kwh, cost))
    db._conn.commit()


def _window(db, km=200.0, soc=(80.0, 60.0)):
    """One trip on the 10th, one on the 20th: the window is 10th 08:00 → 20th 09:00."""
    _trip(db, start="2026-07-10T08:00:00+00:00", end="2026-07-10T09:00:00+00:00",
          km=km / 2, soc=(soc[0], soc[0] - 20))
    _trip(db, start="2026-07-20T08:00:00+00:00", end="2026-07-20T09:00:00+00:00",
          km=km / 2, soc=(soc[1] + 20, soc[1]))


# ── the arithmetic ────────────────────────────────────────────────────────────

def test_the_balance_is_what_went_in_minus_what_stayed(car):
    """80% → 60% of a 50 kWh pack is 10 kWh LESS stored at the end, so the 30 kWh charged in the
    middle understates what was used: 30 − (−10) = 40 kWh over 200 km."""
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=30.0)
    assert db_reader.cost_per_100km()["kwh_100km"] == 20.0


def test_a_window_that_ends_fuller_has_that_energy_taken_off(car):
    """60% → 80% is 10 kWh still in the pack at the end: charged but not consumed, and it must not
    be billed as if it had been. 30 − 10 = 20 kWh over 200 km."""
    _window(car, km=200.0, soc=(60.0, 80.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=30.0)
    assert db_reader.cost_per_100km()["kwh_100km"] == 10.0


def test_his_own_numbers(car):
    """His history, reduced to its boundaries: 63.89 kWh charged over 14 in-window sessions, SoC
    42.7% → 62.9% on an 18.8 kWh pack, 479 km. He gets 12.55; at one decimal that is 12.5."""
    car.set_setting("battery_capacity_kwh", "18.8")
    _window(car, km=479.0, soc=(42.7, 62.9))
    for d in range(1, 15):                      # 14 sessions, 63.89 kWh between them
        _charge(car, start=f"2026-07-{10 + (d % 9):02d}T12:00:00+00:00",
                end=f"2026-07-{10 + (d % 9):02d}T13:00:00+00:00", kwh=63.89 / 14)
    assert db_reader.cost_per_100km()["kwh_100km"] == 12.5


# ── the window, which is the whole reason a charge can be excluded ────────────

def test_a_charge_that_ended_before_the_first_trip_is_not_counted(car):
    """His own July 9 session, ending at 17:33 before a first trip on the 10th at 07:14: that
    energy is already inside the starting SoC. Counting it would bill it twice."""
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=30.0)
    before = db_reader.cost_per_100km()["kwh_100km"]
    _charge(car, start="2026-07-09T16:00:00+00:00", end="2026-07-09T17:33:00+00:00", kwh=25.0)
    assert db_reader.cost_per_100km()["kwh_100km"] == before


def test_a_charge_that_starts_after_the_last_trip_is_not_counted(car):
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=30.0)
    before = db_reader.cost_per_100km()["kwh_100km"]
    _charge(car, start="2026-07-21T08:00:00+00:00", end="2026-07-21T10:00:00+00:00", kwh=25.0)
    assert db_reader.cost_per_100km()["kwh_100km"] == before


def test_a_charge_straddling_the_edge_is_not_counted(car):
    """Half in, half out: its energy is partly inside the boundary SoC already. Whole or nothing."""
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=30.0)
    before = db_reader.cost_per_100km()["kwh_100km"]
    _charge(car, start="2026-07-10T07:00:00+00:00", end="2026-07-10T08:30:00+00:00", kwh=25.0)
    assert db_reader.cost_per_100km()["kwh_100km"] == before


def test_a_charge_still_running_is_not_counted(car):
    """No `ended_at`, so it cannot be shown to sit inside anything — and its energy is still
    arriving. Same rule the Wallbox page just learned."""
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=30.0)
    before = db_reader.cost_per_100km()["kwh_100km"]
    car._conn.execute("INSERT INTO charges (vehicle_id, started_at, start_soc, energy_added_kwh)"
                      " VALUES (1,'2026-07-16T08:00:00+00:00',20,25.0)")
    car._conn.commit()
    assert db_reader.cost_per_100km()["kwh_100km"] == before


# ── when it must say nothing rather than say zero ─────────────────────────────

def test_nothing_charged_inside_the_window_says_nothing(car):
    """The € side still works — this is one key going None, not the card.

    ⚠️ The in-window charge carries a price but no ENERGY figure, which is the only way left to
    reach this branch. It used to be reached with a charge sitting BEFORE the window, back when the
    euros were summed over the whole archive regardless; since #237 such a charge contributes no
    money either, so that fixture proved the card returns nothing at all — a different statement.
    Its own case is now `test_a_charge_before_the_kilometres_pays_for_nothing`."""
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00",
            kwh=None, cost=4.00)
    card = db_reader.cost_per_100km()
    assert card["kwh_100km"] is None
    assert card["total_100km"] == 2.00


def test_a_charge_before_the_kilometres_pays_for_nothing(car):
    """#237, in its smallest form: a charge that ended before the first recorded trip has no
    kilometres of its own to be divided by. Summing it anyway is what turned @nico89612's 152
    typed-in sessions into 4838.43 €/100 km over the 46 km Mate had actually seen."""
    _window(car, km=200.0, soc=(80.0, 60.0))          # trips on the 10th and the 20th
    _charge(car, start="2026-07-09T08:00:00+00:00", end="2026-07-09T10:00:00+00:00", kwh=30.0)
    assert db_reader.cost_per_100km() is None, "no money belongs to those kilometres"
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00",
            kwh=30.0, cost=6.00)
    assert db_reader.cost_per_100km()["total_100km"] == 3.00, "and the in-window one is all of it"


def test_a_balance_that_cancels_out_says_nothing_not_zero(car):
    """Charged 10 kWh and ended 10 kWh fuller: the balance says nothing was consumed, which over
    200 km is not a fact about the car, it is the window not being the whole story. 0.0 kWh/100km
    would be a wrong number printed with confidence."""
    _window(car, km=200.0, soc=(60.0, 80.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=10.0)
    assert db_reader.cost_per_100km()["kwh_100km"] is None


def test_a_negative_balance_says_nothing(car):
    _window(car, km=200.0, soc=(40.0, 90.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=5.0)
    assert db_reader.cost_per_100km()["kwh_100km"] is None


def test_a_charge_with_no_energy_figure_is_missing_not_zero(car):
    """🔴 The rule that has bitten this repo before: an absent signal read as zero. A session Mate
    never got an energy figure for makes the balance INCOMPLETE — it does not make it smaller."""
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=30.0)
    car._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
                      " cost) VALUES (1,'2026-07-16T08:00:00+00:00','2026-07-16T10:00:00+00:00',"
                      "20,60,2.0)")
    car._conn.commit()
    card = db_reader.cost_per_100km()
    assert card["kwh_charges"] == 1, "one session carried an energy figure"
    assert card["kwh_missing"] == 1, "and one did not — said out loud, not silently added as 0"
    assert card["kwh_100km"] == 20.0, "the figure is the same, and it is a FLOOR"


# ── the generator, and why there is no branch for it ──────────────────────────

def test_the_generator_is_not_billed_twice(car):
    """A range-extender window where the pack ends where it started, 20 kWh came off the grid and
    the generator quietly put another 15 in and out again. The balance returns the GRID half —
    20 kWh — because the generator's kWh are already paid for in the litres beside it.

    Modelled the only way the database can show it: the trips took 35 kWh out of the pack while
    only 20 went in from a plug, and the SoC still ends level. Nothing but the generator explains
    that, and the figure must not chase it."""
    car.set_setting("battery_capacity_kwh", "50.0")
    _trip(car, start="2026-07-10T08:00:00+00:00", end="2026-07-10T09:00:00+00:00",
          km=100.0, soc=(80.0, 40.0))
    _trip(car, start="2026-07-20T08:00:00+00:00", end="2026-07-20T09:00:00+00:00",
          km=100.0, soc=(110.0, 80.0))          # impossible alone; the generator refilled it
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=20.0)
    assert db_reader.cost_per_100km()["kwh_100km"] == 10.0     # 20 kWh / 200 km


def test_a_plain_electric_car_gets_it_too(car):
    """No `is_reev` anywhere in the formula — a BEV is the case where `generator` is zero."""
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00", kwh=30.0)
    assert db_reader.cost_per_100km(fuel_l_burned=None)["kwh_100km"] == 20.0


# ── and the card it lives on must be untouched ────────────────────────────────

def test_the_euro_figures_do_not_move(car):
    """The whole point of adding a key is that it adds a key."""
    _window(car, km=200.0, soc=(80.0, 60.0))
    _charge(car, start="2026-07-15T08:00:00+00:00", end="2026-07-15T10:00:00+00:00",
            kwh=30.0, cost=12.0)
    card = db_reader.cost_per_100km()
    assert card["elec_100km"] == 6.0            # 12 € over 200 km
    assert card["total_100km"] == 6.0
    assert card["km"] == 200.0
    assert card["priced_charges"] == 1


def test_the_keys_exist_in_every_language():
    import json
    for path in LOCALES:
        keys = json.loads(path.read_text())
        flat = {k for section in keys.values() if isinstance(section, dict) for k in section}
        for key in ("stats_cost100_kwh", "stats_cost100_nokwh"):
            assert key in flat, f"{path.name} is missing {key}"


def test_the_label_says_what_makes_this_number_different():
    """🔑 The Trips header already shows a kWh/100km, and it is a DIFFERENT number: trip-only,
    against this one's everything-that-left-the-pack. Measured for #207 they are ~28% apart. Two
    correct numbers under one unit is the defect Silvio named — so the difference has to be on
    screen beside the figure, in every language, not in a tooltip."""
    import json
    for path in LOCALES:
        label = json.loads(path.read_text())
        flat = {k: v for s in label.values() if isinstance(s, dict) for k, v in s.items()}
        assert flat["stats_cost100_kwh"].strip(), f"{path.name}: the label is empty"
        assert flat["stats_cost100_kwh"] != flat.get("stats_cost100_elec"), \
            f"{path.name}: reusing the 'electric' label says nothing about standing time"


def test_the_card_shows_it():
    """⚠️ Anchored to `c100.`, the card's OWN jinja variable — the bare string `kwh_100km` is
    already in this template for `reev_total`, so the loose version of this assertion passed on a
    template that renders nothing new. Sixth time this substring trap has been sprung here."""
    body = (ROOT / "web" / "templates" / "statistics.html").read_text()
    assert "c100.kwh_100km" in body, "the balance is computed and never rendered"
