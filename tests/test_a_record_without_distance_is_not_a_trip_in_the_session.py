"""A cloud record with no distance is not a drive, so it cannot stand between a trip and its
official figure.

The cloud's energy figure covers a whole power-on session, so a session holding more than one trip
is refused: attributing it to one of them would overstate that one, and the user is told to merge
them, because only the merged group spreads the figure over the full distance.

The Leapmotor history import writes a trip per cloud record — including the 0 km ones the cloud
keeps for a manoeuvre, with no positions, no SoC and no odometer. Counted as drives, those made the
refusal unfixable: merging the two real halves leaves the 0 km row inside the session and outside
the group, so the conversion refused again and the user had nowhere to go. Measured on Silvio's car:
9 of his 74 shared sessions were blocked by one of those rows alone.

No network: the cloud call is stubbed.
"""
from datetime import datetime, timedelta, timezone

import command_client
import db as D
import db_reader
import ec_enrich

CAPACITY = 65.0


def _rig(tmp_path, monkeypatch):
    """Two drives of 4 km in ONE power-on session, and a 0 km cloud record between them."""
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(CAPACITY)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    a_start = now - timedelta(hours=3)
    spans = {1: (a_start, a_start + timedelta(minutes=10)),
             2: (a_start + timedelta(minutes=17), a_start + timedelta(minutes=30)),
             3: (a_start + timedelta(minutes=31), a_start + timedelta(minutes=32))}
    for tid, (s, e) in spans.items():
        zero = tid == 3
        pdb._conn.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc,"
            " end_soc, efficiency_kwh_100km) VALUES (?,1,?,?,?,?,?,?)",
            (tid, s.isoformat(), e.isoformat(), 0.0 if zero else 4.0,
             None if zero else (95.0 if tid == 1 else 94.0),
             None if zero else (94.0 if tid == 1 else 93.0),
             None if zero else 16.0))
    t = a_start - timedelta(minutes=2)
    while t < a_start:                                   # off before the session
        pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,0)",
                          (t.isoformat(),))
        t += timedelta(seconds=10)
    while t <= spans[3][1]:                              # on, all the way through, never switched off
        pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,1)",
                          (t.isoformat(),))
        t += timedelta(seconds=10)
    pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, ready) VALUES (1,?,0)",
                      ((spans[3][1] + timedelta(seconds=10)).isoformat(),))
    pdb._conn.commit()
    return pdb


def _trip(pdb, tid):
    return dict(pdb._conn.execute("SELECT * FROM trips WHERE id=?", (tid,)).fetchone())


def test_the_session_holds_the_drives_and_not_the_empty_record(tmp_path, monkeypatch):
    pdb = _rig(tmp_path, monkeypatch)
    assert db_reader.ready_session(_trip(pdb, 1))["trip_ids"] == [1, 2]


def test_the_two_drives_still_refuse_to_convert_one_at_a_time(tmp_path, monkeypatch):
    """The guard itself is untouched: two real drives in one session are still named to the user."""
    pdb = _rig(tmp_path, monkeypatch)
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: {"total_kwh": 1.2, "driving_kwh": 1.0, "ac_kwh": 0.1,
                                      "other_kwh": 0.1})
    res = ec_enrich.convert_trip(1)
    assert res["reason"] == "shared_session" and res["other_ids"] == [2]


def test_the_merged_drive_converts(tmp_path, monkeypatch):
    """What the user is told to do now works: merged, the group owns the session and the figure is
    attributed over its whole distance."""
    pdb = _rig(tmp_path, monkeypatch)
    pdb._conn.execute("UPDATE trips SET merged_into_id=1 WHERE id=2")
    pdb._conn.commit()
    monkeypatch.setattr(command_client, "get_energy_breakdown_range",
                        lambda b, e: {"total_kwh": 1.2, "driving_kwh": 1.0, "ac_kwh": 0.1,
                                      "other_kwh": 0.1})
    assert ec_enrich.convert_trip(1) == {"ok": True, "ec": {"total_kwh": 1.2, "driving_kwh": 1.0,
                                                            "ac_kwh": 0.1, "other_kwh": 0.1}}
    assert _trip(pdb, 1)["ec_kwh"] == 1.2 and _trip(pdb, 1)["ec_stable"] == 1
