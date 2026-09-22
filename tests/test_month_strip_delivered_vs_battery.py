"""The Ricariche month strip: the energy told as the pair it always was.

It printed one bare number — "154.93 kWh" — and that number was already a mixture: the wallbox
counter on the home charges that have one, the battery figure everywhere else. Neither word applied
to it, so none was written. Silvio asked for "all the month's gross kWh, the wallbox ones included",
and the honest answer was that the wallbox ones were in there all along; what was missing was the
label, the charger's own kWh (#222), and the other half of the story.

Measured on real data, July: 11 sessions, 10 with a wallbox counter → 129.95 kWh from the meter plus
24.98 from the battery on the one without = 154.93 delivered, against 142.57 that reached the
battery. The 12.36 kWh between them is what the on-board charger turned into heat.
"""
import json
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = (ROOT / "web" / "templates" / "partials" / "charges_calendar_month.html").read_text()
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


# ── one definition of "delivered" ─────────────────────────────────────────────

def test_the_wallbox_counter_wins_at_home():
    assert db_reader._billed_kwh(
        {"location_type": "HOME", "ac_energy_kwh": 30.0, "energy_added_kwh": 27.5}) == 30.0


def test_the_typed_figure_is_used_where_there_is_no_meter():
    """A public charge the owner typed the charger's display into (#222)."""
    assert db_reader._billed_kwh(
        {"location_type": "AC", "ac_energy_kwh": None, "gross_kwh": 41.5,
         "energy_added_kwh": 37.6}) == 41.5


def test_the_battery_figure_is_the_last_resort_not_a_zero():
    """A public charge nobody typed one for still happened. Leaving it out would make the month's
    total drop every time one appeared."""
    assert db_reader._billed_kwh(
        {"location_type": "AC", "ac_energy_kwh": None, "gross_kwh": None,
         "energy_added_kwh": 37.6}) == 37.6


def _group(*pieces, **parent_over):
    """A merged charge the way `_charge_group_stats` hands it to `_billed_kwh`: the parent's row
    with the summed columns, and the pieces beside it."""
    ps = [{"id": i + 1, "location_type": "HOME", "ac_energy_kwh": None, "energy_added_kwh": 0.0,
           "gross_kwh": None, "gross_kwh_from": None, **p} for i, p in enumerate(pieces)]
    g = dict(ps[0], **parent_over)
    for f in ("ac_energy_kwh", "energy_added_kwh"):
        vals = [p[f] for p in ps if p.get(f) is not None]
        g[f] = sum(vals) if vals else None
    g["_pieces"] = ps
    return g


def test_a_merged_charge_is_the_sum_of_the_rule_over_its_pieces():
    """The meter measured the first piece and dropped the second as implausible: on the summed
    columns the group read as "HOME with a counter" and billed 12 for a charge that read 17
    before it was merged."""
    assert db_reader._billed_kwh(_group({"ac_energy_kwh": 12.0, "energy_added_kwh": 10.0},
                                        {"energy_added_kwh": 5.0})) == 17.0


def test_a_piece_of_another_type_keeps_its_own_rule():
    """Merging never changes a piece's type. A HOME piece the meter measured (12) beside an AC
    piece that also carries a counter figure (6, from before it was re-tagged) is 12 + 5: the
    counter only bills a HOME charge. Over the summed columns it read 18."""
    assert db_reader._billed_kwh(_group({"ac_energy_kwh": 12.0, "energy_added_kwh": 10.0},
                                        {"location_type": "AC", "ac_energy_kwh": 6.0,
                                         "energy_added_kwh": 5.0})) == 17.0


def test_a_figure_typed_on_the_merged_charge_counts_once():
    """Typed on the group's card — `gross_kwh_from` names the parent on every piece: it covers all
    of them, above a meter that covers only some."""
    assert db_reader._billed_kwh(_group({"ac_energy_kwh": 12.0, "energy_added_kwh": 10.0,
                                         "gross_kwh": 30.0, "gross_kwh_from": 1},
                                        {"energy_added_kwh": 5.0, "gross_kwh_from": 1})) == 30.0


def test_a_figure_typed_on_one_piece_before_the_merge_stays_that_pieces():
    """Typed on the first piece's own card (it covers itself alone), then merged: 12 for that
    piece, plus the other piece — 17, as before the merge and at the cost of 17."""
    assert db_reader._billed_kwh(_group({"energy_added_kwh": 10.0, "gross_kwh": 12.0,
                                         "gross_kwh_from": 1},
                                        {"energy_added_kwh": 5.0})) == 17.0


def test_a_piece_merged_in_after_the_figure_was_typed_counts_beside_it():
    """30 typed for two pieces, then a third session merged in: the figure never covered it, so it
    counts on its own — 35, whichever of the two ends up the parent (merging makes the earlier row
    the parent and rewrites nothing else)."""
    later = _group({"energy_added_kwh": 10.0, "gross_kwh": 30.0, "gross_kwh_from": 1},
                   {"energy_added_kwh": 5.0, "gross_kwh_from": 1},
                   {"energy_added_kwh": 5.0})
    earlier = _group({"energy_added_kwh": 5.0},
                     {"energy_added_kwh": 10.0, "gross_kwh": 30.0, "gross_kwh_from": 2},
                     {"energy_added_kwh": 5.0, "gross_kwh_from": 2})
    assert db_reader._billed_kwh(later) == db_reader._billed_kwh(earlier) == 35.0


def test_a_figure_typed_on_the_merged_card_supersedes_one_a_piece_was_typed_before():
    """12 typed on the second piece's card, then 17 on the merged card: the 12 stays on its row (an
    unmerge gives it back) and counts for nothing while the 17 covers it — not 29."""
    assert db_reader._billed_kwh(_group({"energy_added_kwh": 10.0, "gross_kwh": 17.0,
                                         "gross_kwh_from": 1},
                                        {"energy_added_kwh": 5.0, "gross_kwh": 12.0,
                                         "gross_kwh_from": 1})) == 17.0


def test_a_meter_that_measured_every_piece_beats_a_typed_figure_like_on_a_single_charge():
    """12 + 6 on the meter, 30 typed on the group: the meter covers the whole plug-in, so it wins —
    the same order as `_billed_kwh` on one charge."""
    assert db_reader._billed_kwh(_group({"ac_energy_kwh": 12.0, "energy_added_kwh": 10.0,
                                         "gross_kwh": 30.0, "gross_kwh_from": 1},
                                        {"ac_energy_kwh": 6.0, "energy_added_kwh": 5.0,
                                         "gross_kwh_from": 1})) == 18.0


def test_a_meter_reading_of_zero_does_not_erase_the_charge():
    assert db_reader._billed_kwh(
        {"location_type": "HOME", "ac_energy_kwh": 0, "energy_added_kwh": 27.5}) == 27.5


def test_there_is_only_one_rule_for_it():
    """It briefly had two — `_billed_kwh` for what Mate reports, a second one for what the calendar
    calls "delivered" — and the two disagreed by exactly the typed figures. Silvio's call (04/08):
    one total, one rule. A second function is how they drift apart again."""
    src = (ROOT / "web" / "db_reader.py").read_text()
    assert "_delivered_kwh" not in src
    assert src.count("def _billed_kwh(") == 1


def test_the_charges_page_totals_take_the_same_rule(env):
    """get_charge_stats used to do it in SQL — a second copy of the rule, and one updated without
    the other is how ENERGIA TOTALE came to disagree with the calendar in the first place. Both
    sums are `_billed_kwh` over the composed charges now, like the calendar.

    Held on the figures, not on the text of the query: this used to grep the function body for
    the gross branch, which pinned the shape of one function and would have passed a helper that
    got the rule wrong. One charge of each kind, and the meter beating a typed figure at home,
    exactly as `_billed_kwh` orders them."""
    _charge(env, 1, 3, 27.5, ac=30.0, cost=9.0)                      # home, wallbox meter
    _charge(env, 2, 9, 37.6, gross=41.5, ctype="AC", cost=20.0)      # public, typed in
    _charge(env, 3, 11, 20.0, ctype="AC")                            # public, nothing to go on
    _charge(env, 4, 15, 20.0, ac=22.0, gross=25.0, cost=7.0)         # home: the meter wins
    rows = db_reader.get_charges(limit=1_000_000)
    stats = db_reader.get_charge_stats()
    assert stats["total_kwh"] == round(sum(db_reader._billed_kwh(r) for r in rows), 2) == 113.5
    assert stats["priced_kwh"] == round(sum(
        db_reader._billed_kwh(r) for r in rows if r.get("cost") is not None), 2) == 93.5


def _piece(env, cid, span, soc, batt, *, ac=None, ctype="AC", cost=None):
    """One piece of a plug-in the car split, on the 3rd: `span` as ('11:10', '12:00'), `soc` as
    (70, 80). Ten minutes apart, rising SoC — what merge_charges accepts."""
    env._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type, cost) VALUES (?,1,?,?,?,?,?,?,?,?)",
        (cid, f"2026-07-03T{span[0]}:00+00:00", f"2026-07-03T{span[1]}:00+00:00", *soc,
         batt, ac, ctype, cost))
    env._conn.commit()


def _second_piece(env, batt, *, ac=None, ctype="AC", cost=None):
    """The rest of a plug-in the car split: ten minutes after the first piece, same day."""
    _piece(env, 2, ("11:10", "12:00"), (70, 80), batt, ac=ac, ctype=ctype, cost=cost)


def _every_total(month=(2026, 7)):
    """The billed total as the Charges tile, the calendar and the AC/DC split each report it."""
    split = db_reader.get_ac_dc_stats()
    return (db_reader.get_charge_stats()["total_kwh"],
            db_reader.get_charges_calendar_month(*month)["total"]["kwh"],
            round(split["ac"]["kwh"] + split["dc"]["kwh"], 2))


def test_a_figure_typed_on_the_merged_charge_is_delivered_once(env):
    """The SQL copy got this wrong: 10 + 5 in the battery, merged, then 30 typed on the merged
    card — summing the rule row by row added the child's battery kWh to a figure that already
    covered it, 35 on the tile against 30 on the calendar and in the AC/DC split. All say 30."""
    _charge(env, 1, 3, 10.0, ctype="AC")
    _second_piece(env, 5.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 30.0)
    assert _every_total() == (30.0, 30.0, 30.0)


def test_a_figure_typed_on_one_piece_before_the_merge_is_not_the_whole_charges(env):
    """The other order: the charger's figure typed on the first piece's own card (12), THEN the
    two pieces merged. Merging changes no figure and no cost, so the total stays 17 — the rows
    say which card the figure was typed on, and this one was a piece's."""
    _charge(env, 1, 3, 10.0, ctype="AC", cost=5.4)
    db_reader.set_charge_gross_kwh(1, 12.0)
    _second_piece(env, 5.0, cost=2.25)
    assert _every_total() == (17.0, 17.0, 17.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    assert _every_total() == (17.0, 17.0, 17.0)


def test_a_merged_home_charge_keeps_the_piece_the_meter_missed(env):
    """12 kWh metered on the first piece, nothing on the second (dropped as implausible), 10 + 5
    in the battery: 17 delivered, before the merge and after it, everywhere the total is shown.
    Over the group's summed columns it read 12 — and the cost, computed per piece, stayed the
    cost of 17."""
    _charge(env, 1, 3, 10.0, ac=12.0, cost=2.4)
    _second_piece(env, 5.0, ctype="HOME", cost=1.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    assert _every_total() == (17.0, 17.0, 17.0)
    stats = db_reader.get_charge_stats()
    assert stats["priced_kwh"] == 17.0
    assert db_reader.get_charges_calendar_month(2026, 7)["total"]["battery_kwh"] == 15.0


def test_a_piece_of_another_type_is_not_billed_on_a_home_meter(env):
    """A HOME piece the meter measured (12) merged with a piece still typed AC that carries a
    counter figure too (6): merging leaves the types alone, and a counter bills HOME only, so it
    is 12 + 5 = 17 after the merge as before it. Over the summed columns it read 18."""
    _charge(env, 1, 3, 10.0, ac=12.0, cost=2.4)
    _second_piece(env, 5.0, ac=6.0, ctype="AC", cost=2.25)
    assert _every_total() == (17.0, 17.0, 17.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    assert _every_total() == (17.0, 17.0, 17.0)


def test_a_fully_metered_charge_typed_and_re_tagged_to_home_is_billed_on_its_meter(env):
    """The owner's own sequence, through the same functions the routes call: two pieces the wallbox
    measured (12 + 6), typed as AC with the charger's figure (30) on the merged card, then re-tagged
    to Home. Every total says 18, like the card — a meter that measured every piece is the figure,
    on one charge and on a merged one alike."""
    _charge(env, 1, 3, 10.0, ac=12.0, ctype="AC")
    _second_piece(env, 5.0, ac=6.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 30.0)
    assert _every_total() == (30.0, 30.0, 30.0), "typed as AC, the typed figure bills"
    db_reader.update_charge_type(1, "HOME")
    assert _every_total() == (18.0, 18.0, 18.0)


def test_a_third_session_merged_into_a_typed_charge_adds_its_own_energy(env):
    """30 typed on the merged card of two pieces (10 + 5), priced 13.50; a third session of 5 kWh,
    priced 2.25, merged into it afterwards. Merging changes no figure and no cost, so the charge
    costs 15.75 for 35 — and the total says 35, not the 30 the figure covered."""
    _charge(env, 1, 3, 10.0, ctype="AC")
    _second_piece(env, 5.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 30.0)
    _piece(env, 3, ("12:10", "13:00"), (80, 90), 5.0, cost=2.25)
    assert _every_total() == (35.0, 35.0, 35.0)
    assert db_reader.merge_charges(1, 3)["ok"]
    assert _every_total() == (35.0, 35.0, 35.0)


def test_a_typed_charge_merged_into_an_earlier_session_keeps_its_figure_once(env):
    """The other direction: the earlier row becomes the parent, and the typed charge — its figure
    on what is now a child — is merged into it. 5 + 30 = 35, not 40 (the figure once and the
    battery kWh of the pieces it covers on top) and not 30."""
    _piece(env, 1, ("09:00", "10:00"), (10, 20), 5.0, cost=2.25)
    _piece(env, 2, ("10:10", "11:00"), (20, 70), 10.0)
    _piece(env, 3, ("11:10", "12:00"), (70, 80), 5.0)
    assert db_reader.merge_charges(2, 3)["ok"]
    db_reader.set_charge_gross_kwh(2, 30.0)
    assert _every_total() == (35.0, 35.0, 35.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    assert _every_total() == (35.0, 35.0, 35.0)
    row = env._conn.execute("SELECT merged_into_id FROM charges WHERE id=2").fetchone()
    assert row[0] == 1, "the earlier row is the parent"


def test_taking_the_figure_back_uncovers_the_pieces(env):
    """A zero is the deliberate way back: the pieces bill on their own again — 15, not 0 and not
    30 — and no row still says it is covered."""
    _charge(env, 1, 3, 10.0, ctype="AC")
    _second_piece(env, 5.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 30.0)
    db_reader.set_charge_gross_kwh(1, 0.0)
    assert _every_total() == (15.0, 15.0, 15.0)
    assert env._conn.execute("SELECT COUNT(*) FROM charges WHERE gross_kwh_from IS NOT NULL").fetchone()[0] == 0


@pytest.mark.parametrize("replacement,expected", [(None, 30.0), (12.0, 17.0), (0.0, 15.0)])
def test_unmerge_and_remerge_preserves_only_the_current_figures_scope(env, replacement, expected):
    _charge(env, 1, 3, 10.0, ctype="AC")
    _second_piece(env, 5.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 30.0)
    assert db_reader.unmerge_charges(1)["ok"]
    db_reader.set_charge_gross_kwh(1, replacement)
    assert db_reader.merge_charges(1, 2)["ok"]
    assert _every_total() == (expected, expected, expected)


def test_a_failed_piece_update_rolls_back_the_figure_scope_and_cost(env):
    import sqlite3

    _charge(env, 1, 3, 10.0, ctype="AC", cost=4.5)
    _second_piece(env, 5.0, cost=2.25)
    assert db_reader.merge_charges(1, 2)["ok"]
    query = "SELECT id,gross_kwh,gross_kwh_from,cost,location_type FROM charges ORDER BY id"
    before = [tuple(r) for r in env._conn.execute(query)]
    env._conn.executescript("""
        CREATE TRIGGER reject_piece_price BEFORE UPDATE OF cost ON charges
        WHEN NEW.id=2 BEGIN SELECT RAISE(ABORT, 'piece update failed'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match="piece update failed"):
        db_reader.set_charge_gross_kwh(1, 30.0)
    assert [tuple(r) for r in env._conn.execute(query)] == before


def test_the_composed_gross_excludes_superseded_figures_without_erasing_them(env):
    _charge(env, 1, 3, 10.0, ctype="AC")
    _second_piece(env, 5.0)
    db_reader.set_charge_gross_kwh(2, 12.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 17.0)
    group = db_reader.get_charges()[0]
    assert group["gross_kwh"] == 17.0
    assert _every_total() == (17.0, 17.0, 17.0)
    assert db_reader.unmerge_charges(1)["ok"]
    assert db_reader.get_charge(2)["gross_kwh"] == 12.0


# ── the column that says which rows a figure covers ──────────────────────────

def test_a_figure_typed_on_a_merged_charge_covers_every_piece(env):
    _charge(env, 1, 3, 10.0, ctype="AC")
    _second_piece(env, 5.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.set_charge_gross_kwh(1, 30.0)
    rows = env._conn.execute("SELECT id, gross_kwh, gross_kwh_from FROM charges ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(1, 30.0, 1), (2, None, 1)]


def test_a_figure_typed_on_one_row_covers_that_row(env):
    _charge(env, 1, 3, 10.0, ctype="AC")
    db_reader.set_charge_gross_kwh(1, 12.0)
    row = env._conn.execute("SELECT gross_kwh, gross_kwh_from FROM charges WHERE id=1").fetchone()
    assert tuple(row) == (12.0, 1)


def test_the_column_is_added_to_an_existing_database_and_old_figures_keep_their_meaning(tmp_path):
    """Before the column, a figure on a row with children was read as the whole plug-in's by the
    calendar and priced as such. The migration says so on those pieces, rather than turning it
    into one row's figure overnight; a figure on any other row covers that row, as it always did;
    a row with no figure is covered by nothing."""
    import sqlite3
    path = str(tmp_path / "old.db")
    D.Database(path)                                   # today's schema…
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE charges DROP COLUMN gross_kwh_from")   # …as it stood before
    con.executemany("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
                    " energy_added_kwh, location_type, gross_kwh, merged_into_id) VALUES"
                    " (?,1,'2026-07-03T09:00:00+00:00','2026-07-03T11:00:00+00:00',20,70,10.0,'AC',?,?)",
                    [(1, 30.0, None), (2, None, 1), (3, 12.0, None), (4, None, None)])
    con.commit(); con.close()
    D.Database(path)
    con = sqlite3.connect(path)
    covered = dict(con.execute("SELECT id, gross_kwh_from FROM charges").fetchall())
    con.close()
    assert covered == {1: 1, 2: 1, 3: 3, 4: None}


# ── the month totals ──────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    pdb._conn.commit()
    return pdb


def _charge(pdb, cid, day, batt, *, ac=None, gross=None, ctype="HOME", cost=None):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, gross_kwh, location_type, cost)"
        f" VALUES (?,1,'2026-07-{day:02d}T09:00:00+00:00','2026-07-{day:02d}T11:00:00+00:00',"
        "20,70,?,?,?,?,?)", (cid, batt, ac, gross, ctype, cost))
    pdb._conn.commit()


def test_the_month_reports_both_sides(env):
    _charge(env, 1, 3, 27.5, ac=30.0)
    _charge(env, 2, 9, 24.98, ctype="AC")
    t = db_reader.get_charges_calendar_month(2026, 7)["total"]
    assert t["kwh"] == 54.98 and t["battery_kwh"] == 52.48 and t["count"] == 2


def test_a_typed_figure_moves_the_delivered_side_only(env):
    _charge(env, 1, 3, 37.6, gross=41.5, ctype="AC")
    t = db_reader.get_charges_calendar_month(2026, 7)["total"]
    assert t["kwh"] == 41.5 and t["battery_kwh"] == 37.6


def test_each_day_carries_the_pair_too(env):
    """The day cells add up to the strip above them — one rule, not two."""
    _charge(env, 1, 3, 27.5, ac=30.0)
    _charge(env, 2, 3, 20.0, ac=22.0)
    _charge(env, 3, 9, 24.98, ctype="AC")
    m = db_reader.get_charges_calendar_month(2026, 7)
    assert m["days"][3]["kwh"] == 52.0 and m["days"][3]["battery_kwh"] == 47.5
    assert round(sum(d["kwh"] for d in m["days"].values()), 2) == m["total"]["kwh"]
    assert round(sum(d["battery_kwh"] for d in m["days"].values()), 2) == m["total"]["battery_kwh"]


def test_another_month_is_not_counted(env):
    _charge(env, 1, 3, 27.5, ac=30.0)
    env._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type) VALUES"
        " (9,1,'2026-08-03T09:00:00+00:00','2026-08-03T11:00:00+00:00',20,70,50.0,55.0,'HOME')")
    env._conn.commit()
    assert db_reader.get_charges_calendar_month(2026, 7)["total"]["kwh"] == 30.0


# ── how the strip says it ─────────────────────────────────────────────────────

def test_the_delivered_total_is_labelled():
    """A bare number was the whole defect: it was neither what you paid for nor what you got."""
    assert "{{ total.kwh | nice }} kWh {{ t('cal_kwh_delivered') }}" in TPL
    assert "t('cal_kwh_delivered_help')" in TPL


def test_the_battery_total_stands_beside_it():
    assert "{{ total.battery_kwh | nice }} {{ t('cal_kwh_in_battery') }}" in TPL


def test_the_second_number_is_hidden_when_it_would_repeat_the_first():
    """A month of public charges with no meter and nothing typed has one number, not two identical
    ones."""
    assert "{% if total.battery_kwh and total.battery_kwh != total.kwh %}" in TPL


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_both_words_exist_in_every_language(path):
    d = json.loads(path.read_text())["translations"]
    for key in ("cal_kwh_delivered", "cal_kwh_in_battery", "cal_kwh_delivered_help"):
        assert d.get(key), f"{path.stem} is missing {key}"


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_words_stay_short_enough_to_sit_on_one_line(path):
    """Three figures and two words share one centred row that already wraps on a phone."""
    d = json.loads(path.read_text())["translations"]
    assert len(d["cal_kwh_delivered"]) <= 14, d["cal_kwh_delivered"]
    assert len(d["cal_kwh_in_battery"]) <= 20, d["cal_kwh_in_battery"]


# ── the rule needs its columns ────────────────────────────────────────────────

def test_every_hand_written_query_asks_for_the_columns_the_rule_reads():
    """`_billed_kwh` reads three columns off a row dict. A caller that SELECTs a subset does not
    fail — `.get()` returns None and the rule quietly slides to the battery branch, which is a wrong
    total that looks perfectly plausible. Caught this way on three queries the day the rule grew its
    middle branch: reev_actual_spend, _trip_stop_charges, get_ac_dc_stats."""
    import re
    src = (ROOT / "web" / "db_reader.py").read_text()
    lines = src.split("\n")
    needed = ("location_type", "ac_energy_kwh", "gross_kwh")
    bad = []
    for i, line in enumerate(lines):
        if "_billed_kwh(" not in line or line.lstrip().startswith(("#", '"', "*")):
            continue
        j = i
        while j > 0 and not lines[j].startswith("def "):
            j -= 1
        body = "\n".join(lines[j:i + 1])
        for sel in re.findall(r'SELECT ([^"]{0,300}?)FROM charges', body):
            if "*" in sel:
                continue
            missing = [c for c in needed if c not in sel]
            if missing:
                bad.append(f"{lines[j].split('(')[0]}: missing {missing}")
    assert not bad, bad


def test_the_ac_dc_split_adds_up_to_the_total_beside_it():
    """Two totals on one screen that do not add up. It summed the battery energy while ENERGIA
    TOTALE summed the billed one — 19.4 kWh apart on the test data, and older than the change that
    exposed it."""
    src = (ROOT / "web" / "db_reader.py").read_text()
    body = src.split("def get_ac_dc_stats(", 1)[1].split("\ndef ", 1)[0]
    assert "_billed_kwh(" in body
    assert 'b["kwh"] += r["energy_added_kwh"]' not in body


def test_the_split_and_the_total_agree_on_real_rows(env):
    _charge(env, 1, 3, 27.5, ac=30.0)                      # home, wallbox meter
    _charge(env, 2, 9, 37.6, gross=41.5, ctype="AC")       # public, typed in
    _charge(env, 3, 11, 12.0, ctype="AC")                  # public, nothing to go on
    split = db_reader.get_ac_dc_stats()
    assert round(split["ac"]["kwh"] + split["dc"]["kwh"], 2) == db_reader.get_charge_stats()["total_kwh"]
