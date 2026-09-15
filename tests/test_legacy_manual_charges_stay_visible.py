"""A charge still stuck on the pre-cost_manual 'MANUAL' placeholder renders as "❓ Da confermare"
on its own badge (CHARGE_TYPES has no 'MANUAL' key, same fallback as a NULL location_type) — but
several OTHER features used to check `location_type IS NULL` specifically, or a bare truthiness of
`location_type`, and so silently disagreed with what the badge itself shows: the "N to confirm"
banner didn't count it, and the "Home vs Public" card counted it as public by construction (it
isn't 'HOME', so `total - home_count` swept it in). Found in review (ProtossBlaster, PR #284):
measured on real test data, a home charge stuck on 'MANUAL' showed up as "Pubblica".

'MANUAL' is deliberately NEVER rewritten by any repair any more (see poller/schema.py's migration
comment) — its real original type cannot be recovered from anything still on the row, so guessing
risks getting it wrong with real money attached. These tests instead hold every OTHER feature to
the one true definition of "not yet confirmed": `location_type IS NULL OR location_type = 'MANUAL'`,
the same test the badge template already uses via `charge_types.get(charge.location_type)`.
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

def test_a_legacy_manual_charge_counts_toward_the_confirm_banner(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="MANUAL", cost=6.0, cost_manual=1)
    _charge(pdb, 2, ctype=None)
    assert db_reader.unconfirmed_charges_count() == 2


def test_the_banner_still_ignores_a_genuinely_confirmed_charge(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME", cost=6.0)
    assert db_reader.unconfirmed_charges_count() == 0


def test_the_banner_link_can_reach_a_legacy_manual_charge_too(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="MANUAL", cost=6.0, cost_manual=1)
    assert db_reader.newest_unconfirmed_charge_id() == 1


# ── the "Home vs Public" card ──────────────────────────────────────────────────

def test_a_legacy_manual_charge_is_neither_home_nor_public(tmp_path, monkeypatch):
    """The actual bug: a home session stuck on 'MANUAL' must not be swept into "Pubblica" just
    because it isn't literally 'HOME' — it isn't confirmed as anything yet. It counts as
    unconfirmed instead, explicitly — see the invariant tests below."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="MANUAL", cost=6.0, cost_manual=1, charge_type="AC")
    stats = db_reader.get_ac_dc_stats()
    assert stats["home_count"] == 0
    assert stats["public_count"] == 0
    assert stats["unconfirmed_count"] == 1
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


# ── the three buckets always add up to the grand total ─────────────────────────
# The actual regression: the card's own donut summed only [home_count, public_count] and silently
# normalised its percentages against that instead of `total`, disagreeing with the text beside it
# (which divides by `total`) and showing the unconfirmed charges nowhere at all.

def test_home_public_and_unconfirmed_always_sum_to_the_grand_total(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME", cost=2.5, charge_type="AC")
    _charge(pdb, 2, ctype="FAST", cost=10.0, charge_type="DC", max_power_kw=50)
    _charge(pdb, 3, ctype="MANUAL", cost=6.0, cost_manual=1, charge_type="AC")
    _charge(pdb, 4, ctype=None, charge_type="DC", max_power_kw=60)
    _charge(pdb, 5, ctype="HOME", cost=3.0, charge_type="DC", max_power_kw=55)   # DC-measured HOME
    stats = db_reader.get_ac_dc_stats()
    assert stats["total"] == 5
    assert (stats["home_count"] + stats["public_count"]
            + stats["unconfirmed_count"]) == stats["total"]
    assert stats["home_count"] == 2
    assert stats["public_count"] == 1
    assert stats["unconfirmed_count"] == 2
