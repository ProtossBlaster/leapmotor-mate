"""A charge still on the pre-cost_manual 'MANUAL' placeholder — every one of them carries the price
its owner typed, and the migration marked it `cost_manual=1`.

Two stories, both still guarded here:

  · PR #284 review (ProtossBlaster): the "Home vs Public" card counted such a charge as public by
    construction (it isn't 'HOME', so `total - home_count` swept it in) — measured on real test
    data, a home charge stuck on 'MANUAL' showed up as "Pubblica". It is never public.
  · v3.16.0 then showed it as "❓ Da confermare" and put it in the banner count — a regression for
    everyone who had used Manual (add-on #2, @termy91it, 19/09/2026): the price WAS the answer. It
    reads "✎ Manual" again, and every count agrees with that badge through ONE definition,
    `db_reader.is_manual_charge` — see tests/test_the_manual_price_is_back_where_it_was.py.

'MANUAL' is deliberately NEVER rewritten by any repair (see poller/schema.py's migration comment):
its real original type cannot be recovered from anything still on the row, so guessing risks
getting it wrong with real money attached. The owner can still pick the type; the price stays.
"""
import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _charge(pdb, cid, *, ctype, cost=None, cost_manual=0, charge_type="AC", max_power_kw=None):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type, cost, cost_manual, charge_type, max_power_kw)"
        " VALUES (?,1,'2026-06-02T16:00:00+00:00','2026-06-02T17:00:00+00:00',40,60,10.0,"
        " ?,?,?,?,?)",
        (cid, ctype, cost, cost_manual, charge_type, max_power_kw))
    pdb._conn.commit()


# ── the "N to confirm" banner ──────────────────────────────────────────────────

def test_a_legacy_manual_charge_is_not_asked_for_again(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="MANUAL", cost=6.0, cost_manual=1)
    _charge(pdb, 2, ctype=None)
    assert db_reader.unconfirmed_charges_count() == 1


def test_the_banner_still_ignores_a_genuinely_confirmed_charge(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME", cost=6.0)
    assert db_reader.unconfirmed_charges_count() == 0


def test_the_banner_link_does_not_lead_to_a_legacy_manual_charge(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="MANUAL", cost=6.0, cost_manual=1)
    assert db_reader.newest_unconfirmed_charge_id() == 0


# ── the "Home vs Public" card ──────────────────────────────────────────────────

def test_a_legacy_manual_charge_is_neither_home_nor_public(tmp_path, monkeypatch):
    """The PR #284 bug: a home session stuck on 'MANUAL' must not be swept into "Pubblica" just
    because it isn't literally 'HOME'. Nor is it waiting (add-on #2): it counts as Manual,
    explicitly — see the invariant tests below."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="MANUAL", cost=6.0, cost_manual=1, charge_type="AC")
    stats = db_reader.get_ac_dc_stats()
    assert stats["home_count"] == 0
    assert stats["public_count"] == 0
    assert stats["manual_count"] == 1
    assert stats["unconfirmed_count"] == 0
    assert stats["total"] == 1   # still counted for AC vs DC — that axis IS known regardless


def test_a_confirmed_public_charge_still_counts_as_public(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="FAST", cost=10.0, charge_type="DC", max_power_kw=50)
    stats = db_reader.get_ac_dc_stats()
    assert stats["public_count"] == 1
    assert stats["public_kwh"] == 10.0
    assert stats["unconfirmed_count"] == 0


def test_a_confirmed_home_charge_never_counts_as_public(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME", cost=2.5, charge_type="AC")
    stats = db_reader.get_ac_dc_stats()
    assert stats["home_count"] == 1
    assert stats["public_count"] == 0
    assert stats["unconfirmed_count"] == 0


def test_a_home_charge_counts_as_home_even_when_measured_dc(tmp_path, monkeypatch):
    """The regression found in review (PR #284, round 4): the type badge offers every type on
    every charge regardless of its own measured current — pick "Casa" on a DC-measured session
    (a fast home charger, a noisy reading, whatever the real reason) and it's HOME by every other
    measure in Mate. home_count used to live nested under `ac` and only count when `not is_dc`, so
    this exact case vanished from the Home vs Public card entirely: not home, not public, not
    unconfirmed, and the card's own total fell a charge short of the page header's. Home vs Public
    is answered by location_type alone now, independent of is_dc."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME", cost=2.5, charge_type="DC", max_power_kw=50)
    stats = db_reader.get_ac_dc_stats()
    assert stats["dc"]["count"] == 1          # still DC for the AC/DC card
    assert stats["home_count"] == 1           # AND home for the Home/Public card
    assert stats["public_count"] == 0
    assert stats["unconfirmed_count"] == 0


def test_a_plain_unconfirmed_charge_is_also_neither_home_nor_public(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype=None, charge_type="AC")
    stats = db_reader.get_ac_dc_stats()
    assert stats["home_count"] == 0
    assert stats["public_count"] == 0
    assert stats["unconfirmed_count"] == 1


# ── the four buckets always add up to the grand total ──────────────────────────
# The actual regression: the card's own donut summed only [home_count, public_count] and silently
# normalised its percentages against that instead of `total`, disagreeing with the text beside it
# (which divides by `total`) and showing the unconfirmed charges nowhere at all.

def test_home_public_manual_and_unconfirmed_always_sum_to_the_grand_total(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME", cost=2.5, charge_type="AC")
    _charge(pdb, 2, ctype="FAST", cost=10.0, charge_type="DC", max_power_kw=50)
    _charge(pdb, 3, ctype="MANUAL", cost=6.0, cost_manual=1, charge_type="AC")
    _charge(pdb, 4, ctype=None, charge_type="DC", max_power_kw=60)
    _charge(pdb, 5, ctype="HOME", cost=3.0, charge_type="DC", max_power_kw=55)   # DC-measured HOME
    stats = db_reader.get_ac_dc_stats()
    assert stats["total"] == 5
    assert (stats["home_count"] + stats["public_count"] + stats["manual_count"]
            + stats["unconfirmed_count"]) == stats["total"]
    assert stats["home_count"] == 2
    assert stats["public_count"] == 1
    assert stats["manual_count"] == 1
    assert stats["unconfirmed_count"] == 1
