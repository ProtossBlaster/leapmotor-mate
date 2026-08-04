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


def test_the_sql_copy_says_the_same_thing():
    """get_charge_stats does it in SQL. Two copies of one rule, and only one of them was updated,
    is how ENERGIA TOTALE came to disagree with the calendar in the first place."""
    src = (ROOT / "web" / "db_reader.py").read_text()
    stats = src.split("def get_charge_stats(", 1)[1].split("\ndef ", 1)[0]
    assert stats.count("WHEN gross_kwh IS NOT NULL AND gross_kwh > 0 THEN gross_kwh") == 2, \
        "the total and the priced-only sum must use the same three branches"


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
