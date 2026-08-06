"""The Wallbox page's totals read the column the poller filled — they do not re-ask Home Assistant.

@Wartopia (#229) saw **141.5 % efficiency** on the three tiles at the top of the Wallbox page:
112.82 kWh from the wall against 159.67 kWh into the battery. His charges were fine — all seven
sit between 1.077 and 1.120 AC/DC, exactly the on-board charger's losses.

The cause is a date, not a number. On **3 June** the tiles were written, and `ac_energy_kwh` did not
exist yet: the only way to know the wall's kWh was to ask Home Assistant for the power sensor's
history and integrate it. On **9 June** the column arrived — the poller now writes the meter's rises
onto every charge, permanently. On **26 July** the calendar was built on the new column. The tiles
were never moved, so since then the same page answers twice: 141.5 % on top, 92.5 % underneath.

Two things follow, and the second is the one nobody had reported:

1. The tiles must read the stored column. Nothing is re-derived, nothing is asked of HA.
2. ⚠️ A charge with a battery figure but NO meter figure must not be counted at all. Adding its DC
   while adding no AC is what pushes the ratio over 100 % — and it is not hypothetical: it is
   exactly what happens after `finalize_charge` drops a runaway or frozen counter (#46, #215), and
   to every HOME charge recorded before the wallbox was configured. The month calendar had the same
   arithmetic and the same hole; it only looked right because @Wartopia's seven charges all have
   both numbers.
"""
import pathlib

import db as PollerDB
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def charges(tmp_path, monkeypatch):
    """A month of HOME charges with a power curve, each with the two energy figures the poller
    stores. `ac=None` means the meter's figure is missing for that one."""
    pdb = PollerDB.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb.ensure_vehicle("LVIN0000000000001", "B10", 2025)

    def add(day, dc, ac):
        cid = day
        pdb._conn.execute(
            "INSERT INTO charges (id, vehicle_id, started_at, ended_at, location_type,"
            " start_soc, end_soc, energy_added_kwh, ac_energy_kwh)"
            " VALUES (?,1,?,?, 'HOME', 20, 40, ?, ?)",
            (cid, f"2026-07-{day:02d}T10:00:00+00:00", f"2026-07-{day:02d}T12:00:00+00:00", dc, ac))
        # the EXISTS gate both readers share: at least one charging position inside the window
        pdb._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, charging, soc,"
            " charge_voltage_v, charge_current_a) VALUES (1,?,1,30,400.0,12.5)",
            (f"2026-07-{day:02d}T11:00:00+00:00",))
        pdb._conn.commit()
    return add


# ── the totals themselves ────────────────────────────────────────────────────
def test_the_totals_come_from_the_stored_columns(charges):
    charges(1, dc=10.0, ac=11.2)
    charges(2, dc=20.0, ac=22.4)
    t = db_reader.wallbox_ac_dc_totals(db_reader._wallbox_home_charges_raw())
    assert t["ac"] == 33.6
    assert t["dc"] == 30.0
    assert t["eff"] == 89.3


def test_a_charge_without_the_meter_figure_is_left_out_of_BOTH(charges):
    """The heart of #229. Its battery kWh must not join a total its wall kWh cannot."""
    charges(1, dc=10.0, ac=11.2)
    charges(2, dc=20.0, ac=None)     # meter dropped by a guard, or not configured yet
    t = db_reader.wallbox_ac_dc_totals(db_reader._wallbox_home_charges_raw())
    assert t["ac"] == 11.2
    assert t["dc"] == 10.0, "the battery kWh of a charge with no meter reading was counted anyway"
    assert t["eff"] == 89.3
    assert t["counted"] == 1 and t["skipped"] == 1


def test_efficiency_can_never_exceed_100(charges):
    """Whatever the mix, the ratio is over one set of charges — so it stays physical."""
    charges(1, dc=10.0, ac=11.2)
    for d in range(2, 8):
        charges(d, dc=20.0, ac=None)
    t = db_reader.wallbox_ac_dc_totals(db_reader._wallbox_home_charges_raw())
    assert t["eff"] <= 100, f"still impossible: {t}"


def test_a_zero_meter_reading_counts_as_missing(charges):
    """0.0 kWh from the wall on a charge that put 20 kWh in the battery is not a measurement."""
    charges(1, dc=10.0, ac=11.2)
    charges(2, dc=20.0, ac=0.0)
    t = db_reader.wallbox_ac_dc_totals(db_reader._wallbox_home_charges_raw())
    assert t["dc"] == 10.0 and t["skipped"] == 1


def test_nothing_to_count_says_nothing(charges):
    charges(1, dc=10.0, ac=None)
    t = db_reader.wallbox_ac_dc_totals(db_reader._wallbox_home_charges_raw())
    assert t["ac"] is None and t["dc"] is None and t["eff"] is None


def test_no_charges_at_all(charges):
    t = db_reader.wallbox_ac_dc_totals([])
    assert t == {"ac": None, "dc": None, "eff": None, "counted": 0, "skipped": 0}


# ── the calendar uses the very same rule ─────────────────────────────────────
def test_the_month_calendar_skips_it_too(charges):
    """It had the same arithmetic and the same hole — it only ever looked right because the charges
    people reported happened to have both numbers."""
    charges(1, dc=10.0, ac=11.2)
    charges(2, dc=20.0, ac=None)
    total = db_reader.get_wallbox_calendar_month(2026, 7)["total"]
    assert total["ac"] == 11.2 and total["dc"] == 10.0
    assert total["eff"] == 89.3


def test_a_day_with_no_meter_figure_shows_no_efficiency(charges):
    charges(3, dc=20.0, ac=None)
    day = db_reader.get_wallbox_calendar_month(2026, 7)["days"].get(3)
    assert day["eff"] is None


def test_the_two_readers_agree_by_construction(charges):
    """The tiles and the calendar sit on the same screen. They now come from one function, so they
    cannot drift apart again — which is exactly how #229 happened."""
    charges(1, dc=10.0, ac=11.2)
    charges(2, dc=20.0, ac=None)
    charges(3, dc=30.0, ac=33.6)
    tiles = db_reader.wallbox_ac_dc_totals(db_reader._wallbox_home_charges_raw())
    month = db_reader.get_wallbox_calendar_month(2026, 7)["total"]
    assert (tiles["ac"], tiles["dc"], tiles["eff"]) == (month["ac"], month["dc"], month["eff"])


# ── and Home Assistant is not asked anything ─────────────────────────────────
def test_the_tiles_no_longer_query_home_assistant():
    """The 3 June code integrated the power sensor's HA history for every session, on every page
    load — including sessions from months ago whose kWh were already in the database."""
    src = (ROOT / "web" / "main.py").read_text()
    body = src.split("def _wallbox_totals", 1)[1].split("\n@app", 1)[0]
    assert "_session_energy" not in body, "still re-deriving instead of reading the column"
    assert "get_history" not in body
    assert "wallbox_ac_dc_totals" in body


# ── the day drawer and the sessions tree read the same column ────────────────
def test_the_day_drawer_carries_the_stored_figures(charges):
    """It used to fetch Home Assistant's history per session, on every click. Same page as the
    tiles above it, so the same charge could be described twice, differently."""
    charges(3, dc=10.0, ac=11.2)
    s = db_reader.get_wallbox_calendar_day(2026, 7, 3)[0]
    assert s["dc_kwh"] == 10.0 and s["ac_kwh"] == 11.2 and s["eff"] == 89.3


def test_a_session_with_no_meter_figure_shows_no_efficiency(charges):
    charges(3, dc=10.0, ac=None)
    s = db_reader.get_wallbox_calendar_day(2026, 7, 3)[0]
    assert s["dc_kwh"] == 10.0 and s["ac_kwh"] is None and s["eff"] is None


def test_neither_view_asks_home_assistant_any_more():
    # ⚠️ Anchored to the CALL, not the bare name: `db_reader.wallbox_session_energy` contains
    # "_session_energy" as a substring, and the loose version of this assertion failed on the
    # correct code. The same substring trap that has cost four wrong asserts on this project.
    import re
    src = (ROOT / "web" / "main.py").read_text()
    assert not re.search(r"(?<![a-z_])_session_energy\(", src), \
        "the Home Assistant re-derivation is being called again"
    assert "def _session_energy" not in src, "dead code left behind for someone to re-wire"
    for fn in ("_wallbox_day_sessions", "_wallbox_sessions_grouped"):
        body = src.split(f"def {fn}", 1)[1].split("\n@app", 1)[0].split("\ndef ", 1)[0]
        assert "get_history" not in body, f"{fn} still queries Home Assistant"


def test_the_sessions_tree_rolls_up_the_same_way(charges):
    """One charge with both figures, one without: the day/month/year totals must skip the second
    exactly as the tiles do, or the tree contradicts the card above it."""
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")
    import main as W
    charges(3, dc=10.0, ac=11.2)
    charges(4, dc=20.0, ac=None)
    tree = W._wallbox_sessions_grouped()
    year = tree[0]
    assert year["ac"] == 11.2 and year["dc"] == 10.0 and year["eff"] == 89.3


# ── a charge still running is not a comparison yet ───────────────────────────
def _in_progress(pdb, day, dc, ac):
    """A charge with no `ended_at`: the meter is behind the car, as it always is mid-session."""
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, location_type,"
        " start_soc, end_soc, energy_added_kwh, ac_energy_kwh) VALUES (?,1,?,NULL,'HOME',20,32,?,?)",
        (90 + day, f"2026-07-{day:02d}T10:00:00+00:00", dc, ac))
    pdb._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, charging, soc, charge_voltage_v,"
        " charge_current_a) VALUES (1,?,1,25,400.0,12.5)", (f"2026-07-{day:02d}T10:30:00+00:00",))
    pdb._conn.commit()


def test_a_charge_still_running_is_left_out(charges, tmp_path, monkeypatch):
    """Silvio, 06/08/26: «si calcolano solo le ricariche completate». While the energy is still
    flowing the meter lags the car — @Wartopia's 6 August read 2.74 kWh against 4.03 — so counting
    it makes the efficiency drift for as long as the cable is in. Measured before this guard: 89.3 %
    became 93.6 % the moment a live charge joined."""
    import db as PollerDB
    charges(1, dc=10.0, ac=11.2)
    charges(2, dc=20.0, ac=22.4)
    pdb = PollerDB.Database(str(tmp_path / "t.db"))
    _in_progress(pdb, 9, dc=4.03, ac=2.74)
    t = db_reader.wallbox_ac_dc_totals(db_reader._wallbox_home_charges_raw())
    assert t["counted"] == 2, "the live charge joined the comparison"
    assert t["eff"] == 89.3


def test_the_live_charge_is_gone_from_every_view(charges, tmp_path, monkeypatch):
    import db as PollerDB
    charges(9, dc=10.0, ac=11.2)
    pdb = PollerDB.Database(str(tmp_path / "t.db"))
    _in_progress(pdb, 9, dc=4.03, ac=2.74)
    assert len(db_reader.get_wallbox_calendar_day(2026, 7, 9)) == 1, "the drawer still lists it"
    assert db_reader.get_wallbox_calendar_month(2026, 7)["total"]["counted"] == 1
