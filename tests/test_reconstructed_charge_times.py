"""#501 (@matttiaromano): the start and end of a RECONSTRUCTED charge can be corrected.

A recovered charge spans the gap between the last reading before the car went quiet and the first
after it — 19/11 17:17 → 23/11 09:53 for a charge the wallbox really ran on Saturday at 22:00.
Mate cannot know better, the owner can. So the owner may move the two times, and only those: the
SoC and the energy are readings at the two ends of the gap, and they stay what the car reported.

The new times must stay inside the span Mate reconstructed. That span is the bound the readings
prove, and it is also what keeps "Recover missed charges" from adding the same charge twice: its
overlap check finds the corrected charge only while the charge still overlaps the gap it came from.

Poller schema + db_reader on a tmp DB → CI-safe.
"""
import json
import pathlib

import db as D
import db_reader

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"

START = "2026-11-19T17:17:00+00:00"
END = "2026-11-23T09:53:00+00:00"


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(67.0)
    pdb.ensure_vehicle("LVIN0000000000001", "T03", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _row(pdb, cid):
    return pdb._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone()


def _recovered(pdb, cid=1, location_type="HOME", cost=3.15, cost_manual=0, start=START, end=END):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, duration_min, charge_type, location_type, cost, cost_manual, reconstructed)"
        " VALUES (?,1,?,?,41,76,12.6,5196.0,'AC',?,?,?,1)",
        (cid, start, end, location_type, cost, cost_manual))
    pdb._conn.commit()


def test_times_move_inside_the_span(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _recovered(pdb)
    err = db_reader.set_reconstructed_charge_times(1, "2026-11-22T21:00:00+00:00", "2026-11-23T03:00:00+00:00")
    assert err is None
    r = _row(pdb, 1)
    assert r["started_at"] == "2026-11-22T21:00:00+00:00"
    assert r["ended_at"] == "2026-11-23T03:00:00+00:00"
    assert r["duration_min"] == 360.0
    assert (r["start_soc"], r["end_soc"], r["energy_added_kwh"]) == (41, 76, 12.6)
    assert r["reconstructed"] == 1


def test_the_span_ends_are_allowed(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _recovered(pdb)
    assert db_reader.set_reconstructed_charge_times(1, START, "2026-11-20T01:00:00+00:00") is None
    _recovered(pdb, cid=2, start="2026-11-24T17:17:00+00:00", end="2026-11-25T09:53:00+00:00")
    assert db_reader.set_reconstructed_charge_times(2, "2026-11-25T01:00:00+00:00",
                                                    "2026-11-25T09:53:00+00:00") is None


def test_a_start_before_the_span_is_refused(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _recovered(pdb)
    err = db_reader.set_reconstructed_charge_times(1, "2026-11-19T17:16:00+00:00", "2026-11-20T01:00:00+00:00")
    assert err == "charge_times_outside"
    assert _row(pdb, 1)["started_at"] == START


def test_an_end_after_the_span_is_refused(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _recovered(pdb)
    err = db_reader.set_reconstructed_charge_times(1, "2026-11-22T21:00:00+00:00", "2026-11-23T09:54:00+00:00")
    assert err == "charge_times_outside"
    assert _row(pdb, 1)["ended_at"] == END


def test_an_end_not_after_the_start_is_refused(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _recovered(pdb)
    t = "2026-11-22T21:00:00+00:00"
    assert db_reader.set_reconstructed_charge_times(1, t, t) == "charge_times_order"
    assert db_reader.set_reconstructed_charge_times(1, t, "2026-11-22T20:00:00+00:00") == "charge_times_order"
    assert _row(pdb, 1)["started_at"] == START


def test_an_impossible_power_is_refused(tmp_path, monkeypatch):
    """12.6 kWh in one minute is 756 kW: no charger does that, the poller drops it as a glitch."""
    pdb = _setup(tmp_path, monkeypatch)
    _recovered(pdb)
    err = db_reader.set_reconstructed_charge_times(1, "2026-11-22T21:00:00+00:00", "2026-11-22T21:01:00+00:00")
    assert err == "charge_times_power"
    assert _row(pdb, 1)["started_at"] == START


def test_a_measured_charge_is_refused(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _recovered(pdb)
    pdb._conn.execute("UPDATE charges SET reconstructed=0 WHERE id=1")
    pdb._conn.commit()
    err = db_reader.set_reconstructed_charge_times(1, "2026-11-22T21:00:00+00:00", "2026-11-23T03:00:00+00:00")
    assert err == "charge_times_not_reconstructed"
    assert _row(pdb, 1)["started_at"] == START


def test_a_typed_in_charge_is_refused(tmp_path, monkeypatch):
    """A typed-in charge has its own form, which edits everything; this one edits only the times."""
    pdb = _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-11-20T12:00:00", 10.0)
    err = db_reader.set_reconstructed_charge_times(cid, "2026-11-20T12:00:00+00:00", "2026-11-20T14:00:00+00:00")
    assert err == "charge_times_not_reconstructed"


def test_a_missing_charge_is_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert db_reader.set_reconstructed_charge_times(99, START, END) == "charge_times_not_reconstructed"


def test_a_merged_charge_is_refused(tmp_path, monkeypatch):
    """A merged charge's span is its pieces'; moving the one row would leave them behind."""
    pdb = _setup(tmp_path, monkeypatch)
    _recovered(pdb, cid=1)
    _recovered(pdb, cid=2, start="2026-11-23T10:00:00+00:00", end="2026-11-23T12:00:00+00:00")
    pdb._conn.execute("UPDATE charges SET merged_into_id=1 WHERE id=2")
    pdb._conn.commit()
    for cid in (1, 2):
        err = db_reader.set_reconstructed_charge_times(cid, "2026-11-22T21:00:00+00:00", "2026-11-23T03:00:00+00:00")
        assert err == "charge_times_merged"
    assert _row(pdb, 1)["started_at"] == START


def test_the_cost_follows_the_new_start_on_a_start_band_tariff(tmp_path, monkeypatch):
    """The wallbox waited for the night tariff. Priced at the reconstructed 17:17 the charge paid the
    day price; at its real 22:00 start it pays the night one."""
    pdb = _setup(tmp_path, monkeypatch)
    db_reader.set_timezone("UTC")
    db_reader.set_setting("price_home_kwh", "0.25")
    db_reader.set_setting("cost_mode", "tou")
    db_reader.set_setting("tou_method", "start")
    db_reader.set_setting("tou_bands", json.dumps(
        [{"start": "22:00", "end": "06:00", "prices": {"HOME": 0.10}, "days": [0, 1, 2, 3, 4, 5, 6]}]))
    _recovered(pdb, cost=3.15)                       # 12.6 kWh × 0.25 at 17:17
    assert db_reader.set_reconstructed_charge_times(1, "2026-11-22T22:00:00+00:00", "2026-11-23T03:00:00+00:00") is None
    assert _row(pdb, 1)["cost"] == 1.26             # 12.6 kWh × 0.10


def test_a_price_typed_by_hand_is_kept(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    db_reader.set_setting("price_home_kwh", "0.25")
    _recovered(pdb, cost=2.0, cost_manual=1)
    assert db_reader.set_reconstructed_charge_times(1, "2026-11-22T22:00:00+00:00", "2026-11-23T03:00:00+00:00") is None
    r = _row(pdb, 1)
    assert (r["cost"], r["cost_manual"]) == (2.0, 1)


def test_the_card_offers_the_form_only_on_a_reconstructed_charge():
    src = (TEMPLATES / "partials" / "charge_card.html").read_text(encoding="utf-8")
    assert 'hx-post="api/charges/{{ c.id }}/times"' in src
    i = src.index('hx-post="api/charges/{{ c.id }}/times"')
    assert src.rfind("{% if c.reconstructed and not c.manual_entry %}", 0, i) != -1
