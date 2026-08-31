"""#188 (@adoewa): a charge you TYPED IN can be corrected — times, SoC, energy, cost.

Two things had to be true before the form could exist. Mate has to know which rows were typed,
and `location_type='MANUAL'` cannot answer that: it doubles as the COST BASIS a user picks to
type the price of a real, measured session. Gating on it would have handed out the timestamps and
SoC the car reported, to be rewritten by a form. Hence `manual_entry`, and hence the second half
of these tests, which check what the edit REFUSES rather than what it changes.

The other half is the display: a typed-in charge carries no SoC, and the card used to draw the
missing value as a measured `0.0% → 0.0%` with a yellow `+0.0%` beside it.

Poller schema + db_reader on a tmp DB → CI-safe.
"""
import pathlib

import pytest

import db as D
import db_reader

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(67.0)
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _row(pdb, cid):
    return pdb._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone()


def _measured(pdb, cid, location_type=None):
    """A charge the poller recorded: it has telemetry — lat/lon at the start, duration and peak
    power at the end. `location_type` is free to be anything the user picked, MANUAL included."""
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, duration_min, latitude, longitude, max_power_kw, charge_type,"
        " location_type, cost) VALUES (?,1,'2026-07-02T04:45:00+00:00','2026-07-02T06:15:00+00:00',"
        " 40,72,20.5,90.0,45.46,9.19,7.4,'AC',?,12.5)", (cid, location_type))
    pdb._conn.commit()


# ── the marker ───────────────────────────────────────────────────────────────
def test_migration_adds_manual_entry_column(tmp_path):
    pdb = D.Database(str(tmp_path / "t.db"))
    cols = {r[1] for r in pdb._conn.execute("PRAGMA table_info(charges)").fetchall()}
    assert "manual_entry" in cols


def test_typed_in_charge_is_marked(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-05-01T12:00:00", 24.5, cost=6.0)
    assert _row(pdb, cid)["manual_entry"] == 1


def test_measured_charge_tagged_manual_for_its_cost_is_not_marked(tmp_path, monkeypatch):
    """The whole reason the column exists. Picking "Manual" on the badge means "I'll type the
    price" — it must never make the car's own timestamps editable."""
    pdb = _setup(tmp_path, monkeypatch)
    _measured(pdb, 7, location_type="MANUAL")
    assert (_row(pdb, 7)["manual_entry"] or 0) == 0


def test_backfill_marks_old_typed_rows_and_leaves_measured_ones(tmp_path):
    """Existing installs have no marker, so the migration reconstructs it from the signature a
    typed-in row has always had. @adoewa's imported history has to come out marked, and the
    charge he tagged MANUAL to type its price has to come out unmarked."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    # Simulate a pre-#188 database: drop the marker and write both kinds of row without it.
    pdb._conn.execute("ALTER TABLE charges RENAME TO charges_old")
    cols = [r[1] for r in pdb._conn.execute("PRAGMA table_info(charges_old)").fetchall()]
    keep = [c for c in cols if c != "manual_entry"]
    pdb._conn.execute(f"CREATE TABLE charges AS SELECT {', '.join(keep)} FROM charges_old")
    pdb._conn.execute("DROP TABLE charges_old")
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh,"
        " charge_type, location_type, cost) VALUES"
        " (1,1,'2026-07-02T04:45:00+00:00','2026-07-02T04:45:00+00:00',46.3,'AC','MANUAL',14.0)")
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, duration_min, latitude, longitude, max_power_kw, charge_type,"
        " location_type, cost) VALUES (2,1,'2026-07-03T04:45:00+00:00','2026-07-03T06:15:00+00:00',"
        " 40,72,20.5,90.0,45.46,9.19,7.4,'AC','MANUAL',12.5)")
    pdb._conn.commit()
    pdb._conn.close()

    reopened = D.Database(path)        # the migration runs here
    rows = {r["id"]: r["manual_entry"] for r in
            reopened._conn.execute("SELECT id, manual_entry FROM charges").fetchall()}
    assert rows[1] == 1                # typed in: MANUAL basis and no telemetry at all
    assert (rows[2] or 0) == 0         # measured: has lat/lon, duration and peak power


# ── the edit ─────────────────────────────────────────────────────────────────
def test_edit_rewrites_every_field(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-07-02T04:45:00+00:00", 46.3, cost=14.0, charge_type="AC")
    assert db_reader.update_manual_charge(
        cid, "2026-07-02T04:45:00+00:00", 41.0, cost=12.30, charge_type="DC",
        ended_at="2026-07-02T06:15:00+00:00", start_soc=18.0, end_soc=80.0)
    r = _row(pdb, cid)
    assert r["energy_added_kwh"] == 41.0
    assert r["cost"] == 12.30
    assert r["charge_type"] == "DC"
    assert r["ended_at"] == "2026-07-02T06:15:00+00:00"
    assert (r["start_soc"], r["end_soc"]) == (18.0, 80.0)


def test_edit_refuses_a_measured_charge(tmp_path, monkeypatch):
    """Even when it carries the MANUAL cost basis — the guard is the marker, not the basis."""
    pdb = _setup(tmp_path, monkeypatch)
    _measured(pdb, 7, location_type="MANUAL")
    before = dict(_row(pdb, 7))
    assert db_reader.update_manual_charge(7, "2020-01-01T00:00:00+00:00", 999.0,
                                          start_soc=1.0, end_soc=2.0) is False
    after = dict(_row(pdb, 7))
    assert after == before             # nothing moved


def test_end_time_gives_the_card_a_duration(tmp_path, monkeypatch):
    """An end time the user finally gets to enter has to produce the ⏱ duration too, or the card
    prints two times with nothing between them."""
    pdb = _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-07-02T04:45:00+00:00", 46.3)
    assert _row(pdb, cid)["duration_min"] is None          # no end given → no span
    db_reader.update_manual_charge(cid, "2026-07-02T04:45:00+00:00", 46.3,
                                   ended_at="2026-07-02T06:15:00+00:00")
    assert _row(pdb, cid)["duration_min"] == 90.0


def test_no_end_means_no_invented_duration(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-07-02T04:45:00+00:00", 46.3,
                                      ended_at="2026-07-02T06:15:00+00:00")
    assert _row(pdb, cid)["duration_min"] == 90.0
    db_reader.update_manual_charge(cid, "2026-07-02T04:45:00+00:00", 46.3, ended_at=None)
    r = _row(pdb, cid)
    assert r["duration_min"] is None
    assert r["ended_at"] == r["started_at"]     # still "ended" so it counts in the totals


def test_edited_charge_still_counts_in_the_totals(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-05-01T12:00:00", 24.5, cost=6.0)
    db_reader.update_manual_charge(cid, "2026-05-01T12:00:00", 30.0, cost=9.0)
    s = db_reader.get_stats_summary()
    assert s["charge_count"] == 1
    assert abs((s["total_kwh_charged"] or 0) - 30.0) < 0.01
    assert abs((s["total_cost"] or 0) - 9.0) < 0.01


# ── the display (#188, second half) ──────────────────────────────────────────
jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")


def _card(**over):
    c = {"id": 1, "started_at": "2026-07-02T06:45:00+02:00", "ended_at": "2026-07-02T06:45:00+02:00",
         "start_soc": None, "end_soc": None, "energy_added_kwh": 46.3, "cost": 14.0,
         "duration_min": None, "max_power_kw": None, "charge_type": "AC", "location_type": "MANUAL",
         "manual_entry": 1, "ac_energy_kwh": None, "is_free": 0, "reconstructed": 0, "note": ""}
    c.update(over)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["money"] = lambda v: f"{v:.2f} €"
    env.filters["dec"] = lambda v, n=1: "\u2014" if v is None else f"{float(v):.{n}f}"
    return env.get_template("partials/charge_card.html").render(
        # `gross_kwh_ok` is a template GLOBAL in the app (templates.env.globals), not a route
        # variable — that is what stopped the pencil vanishing from the day drawer. A test
        # environment has to mirror it, or the card renders against a Jinja that does not
        # have it.
        gross_kwh_ok=lambda: True,
        solar_kwh_ok=lambda: True, solar_mode_on=lambda: False,
        # The price box is labelled in the reader's own money, so every context that
        # renders this card carries the currency — including the four routes that did not.
        currency=db_reader.CURRENCIES["EUR"],
        c=c, t=lambda k: k, charge_types=db_reader.CHARGE_TYPES,
        fmt_dur=lambda v: "—" if v is None else f"{v:.0f} min")


def test_missing_soc_is_not_drawn_as_zero(tmp_path, monkeypatch):
    """@adoewa's screenshot: SOC 0.0% → 0.0%, +0.0%, on a charge that never carried a SoC.
    Correct values, invented measurement."""
    out = _card()
    assert "0.0%" not in out
    assert "+0.0" not in out
    assert "soc_unknown_hint" in out          # the em dash carries the explanation
    assert "soc-bar" not in out               # and no gain bar is painted


def test_known_soc_still_shows_the_numbers_and_the_bar(tmp_path, monkeypatch):
    out = _card(start_soc=18.0, end_soc=80.0)
    assert "18.0%" in out and "80.0%" in out
    assert "+62.0" in out
    assert "soc-bar" in out


def test_a_real_zero_soc_is_still_shown(tmp_path, monkeypatch):
    """0 is a legitimate reading — only None is unknown. `or 0` could not tell them apart, which
    is exactly how the invented value got in."""
    out = _card(start_soc=0.0, end_soc=12.0)
    assert "0.0%" in out and "12.0%" in out
    assert "soc_unknown_hint" not in out


def test_edit_form_is_offered_on_a_typed_in_charge(tmp_path, monkeypatch):
    out = _card()
    assert 'hx-post="api/charges/1/edit"' in out
    for field in ('name="date"', 'name="time"', 'name="end_date"', 'name="end_time"',
                  'name="energy"', 'name="cost"', 'name="start_soc"', 'name="end_soc"',
                  'name="charge_type"'):
        assert field in out


def test_edit_form_is_absent_on_a_measured_charge(tmp_path, monkeypatch):
    """The UI must not offer what the backend would refuse."""
    out = _card(manual_entry=0, start_soc=40.0, end_soc=72.0, duration_min=90.0)
    assert "/edit" not in out
    assert 'name="start_soc"' not in out


def test_edit_form_is_prefilled_with_what_is_there(tmp_path, monkeypatch):
    out = _card(start_soc=18.0, end_soc=80.0, ended_at="2026-07-02T08:15:00+02:00")
    assert 'value="2026-07-02"' in out         # date
    assert 'value="06:45"' in out              # start time
    assert 'value="08:15"' in out              # end time, since it differs from the start
    assert 'value="46.30"' in out              # energy
    assert 'value="18.0"' in out and 'value="80.0"' in out


def test_absent_end_is_not_prefilled_as_the_start(tmp_path, monkeypatch):
    """add_manual_charge stores ended_at = started_at when no end is known. Showing that back as
    an end time would turn "unknown" into a claim the charge lasted zero minutes."""
    out = _card()                              # started_at == ended_at
    assert out.count('value="06:45"') == 1     # the start only
