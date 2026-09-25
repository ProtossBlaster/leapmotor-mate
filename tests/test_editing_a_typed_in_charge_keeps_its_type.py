"""Correcting a typed-in charge must not retype it (#309).

@arzthilfe: a charge he had entered by hand and marked **Home** comes back as **AC** the moment he
edits anything on it — the cost, a time, the SoC. He changes one number and loses the type.

The cause is two halves of the same form. The edit panel's selector offers **AC and DC only**
(`partials/charge_card.html`), while `update_manual_charge` writes

    loc_type = "FAST" if ct == "DC" else "AC"

into `location_type` on every save. So a charge typed as Home, HPC or Free has no way to say so on
the way back in, and the handler overwrites it with the only two answers the form can give.

The type is what the cost is computed from (`PRICE_KEYS`), what the statistics count by, and what
the Charges page filters on — so this is not a label going astray, it is a charge moving from one
column of the accounts to another because its owner corrected a typo.

The fix is for the form to offer the same five types the rest of Mate uses, and for the handler to
keep the one it is given. The AC/DC tag stays derived — Home and Free are AC, HPC and DC are DC —
because that one describes the socket, not the place.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import db as poller_db
import db_reader
import pytest


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def typed(tmp_path, monkeypatch):
    """One charge he typed in himself and marked Home."""
    path = str(tmp_path / "i309.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'C10')")
    con.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh, "
        "charge_type, location_type, manual_entry, cost, cost_manual) "
        "VALUES (1, 1, ?, ?, 18.4, 'AC', 'HOME', 1, 4.60, 1)", (_iso(3), _iso(3)))
    con.commit(); con.close()
    return path


def _type_of(path, charge_id=1):
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    row = con.execute("SELECT charge_type, location_type FROM charges WHERE id = ?",
                      (charge_id,)).fetchone()
    con.close()
    return row["location_type"], row["charge_type"]


def test_correcting_the_cost_does_not_retype_a_home_charge(typed):
    """He edits the amount and nothing else; the charge must still be a Home charge."""
    assert db_reader.update_manual_charge(1, started_at=_iso(3), energy_kwh=18.4,
                                          cost=5.10, charge_type="HOME")
    assert _type_of(typed) == ("HOME", "AC")


@pytest.mark.parametrize("chosen,expected_loc,expected_tag", [
    ("HOME", "HOME", "AC"),
    ("AC",   "AC",   "AC"),
    ("FREE", "FREE", "AC"),
    ("FAST", "FAST", "DC"),
    ("HPC",  "HPC",  "DC"),
])
def test_every_type_the_rest_of_mate_uses_survives_the_round_trip(typed, chosen,
                                                                  expected_loc, expected_tag):
    """The five of CHARGE_TYPES, not two. The AC/DC tag stays derived from the type."""
    assert db_reader.update_manual_charge(1, started_at=_iso(3), energy_kwh=18.4,
                                          cost=4.60, charge_type=chosen)
    assert _type_of(typed) == (expected_loc, expected_tag)


def test_the_old_two_answers_still_mean_what_they_meant(typed):
    """A form, a script or an older page that still sends DC keeps working."""
    assert db_reader.update_manual_charge(1, started_at=_iso(3), energy_kwh=18.4,
                                          cost=4.60, charge_type="DC")
    assert _type_of(typed) == ("FAST", "DC")


def test_both_forms_offer_the_same_five_types(typed):
    """The handler can only keep a type the form can send, so the selectors are half the fix — and
    a test on the handler alone would pass over a page that still offers two. Read from the PAGE
    Mate serves, not from the template source: the options are built in a loop now, and markup that
    looks right can still render wrong."""
    import re
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    pytest.importorskip("fastapi")
    import main
    from starlette.testclient import TestClient
    db_reader.set_setting("setup_complete", "1")
    html = TestClient(main.app).get("/charges").text
    selects = re.findall(r'<select name="charge_type".*?</select>', html, re.S)
    assert selects, "no charge-type selector on the Charges page"
    for block in selects:
        offered = set(re.findall(r'<option value="([A-Z]+)"', block))
        assert offered == set(db_reader.CHARGE_TYPES), \
            f"a form offers {sorted(offered)}, Mate types charges as {sorted(db_reader.CHARGE_TYPES)}"


def test_a_measured_charge_is_still_refused(tmp_path, monkeypatch):
    """The guard that matters: on a charge the car reported, these fields are readings."""
    path = str(tmp_path / "measured.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'C10')")
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, energy_added_kwh, "
                "location_type, manual_entry) VALUES (1, 1, ?, 10.0, 'HOME', 0)", (_iso(1),))
    con.commit(); con.close()
    assert not db_reader.update_manual_charge(1, started_at=_iso(1), energy_kwh=99.0,
                                              charge_type="HPC")
