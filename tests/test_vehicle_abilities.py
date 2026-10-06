"""The car's DECLARED abilities (#67). The poller persists the VehicleAbility codes the vehicle itself
reports, and the diagnostic renders them — so remote-control gaps ("the T03 ignores the fan/off command")
can be told apart from "this model simply doesn't have that feature", and a new model (e.g. the B05) is
understood the moment it connects instead of assumed. CI-safe: in-memory DB / stubbed get_vehicle."""
import json

import db as D
import db_reader
import diagnostics


# ── poller persistence ────────────────────────────────────────────────────────────────────────────
def test_ensure_vehicle_persists_abilities_sorted_deduped():
    pdb = D.Database(":memory:")
    vid = pdb.ensure_vehicle("VIN123", "T03", 2024, abilities=[17, 6, 6, 2])
    row = pdb._conn.execute("SELECT abilities FROM vehicles WHERE id=?", (vid,)).fetchone()
    assert json.loads(row["abilities"]) == [2, 6, 17]      # deduped + sorted


def test_ensure_vehicle_refreshes_and_none_does_not_wipe():
    pdb = D.Database(":memory:")
    pdb.ensure_vehicle("VIN123", "T03", None, abilities=[1, 2, 3])
    pdb.ensure_vehicle("VIN123", "T03", None, abilities=[1, 2, 3, 9])   # a later start updates
    row = pdb._conn.execute("SELECT abilities FROM vehicles WHERE vin='VIN123'").fetchone()
    assert json.loads(row["abilities"]) == [1, 2, 3, 9]
    pdb.ensure_vehicle("VIN123", "T03", None, abilities=None)           # lib reported nothing → keep
    row = pdb._conn.execute("SELECT abilities FROM vehicles WHERE vin='VIN123'").fetchone()
    assert json.loads(row["abilities"]) == [1, 2, 3, 9]


def test_ensure_vehicle_without_abilities_is_backward_compatible():
    pdb = D.Database(":memory:")
    vid = pdb.ensure_vehicle("VIN123", "B10")                            # old call site, no abilities
    row = pdb._conn.execute("SELECT abilities FROM vehicles WHERE id=?", (vid,)).fetchone()
    assert row["abilities"] is None


# ── diagnostic rendering ──────────────────────────────────────────────────────────────────────────
def test_abilities_section_flags_missing_comfort(monkeypatch):
    # A T03-like set: base climate (AC_ON=6) but NO heated/vented seats (14/21/42/43) nor steering (15).
    monkeypatch.setattr(db_reader, "get_vehicle",
                        lambda: ({"abilities": json.dumps([1, 2, 6, 11])}, {}))
    out = diagnostics._abilities_section()
    assert "SEAT_HEAT=✗" in out
    assert "SEAT_VENT_DRV=✗" in out and "SEAT_VENT_PAS=✗" in out
    assert "STEERING_HEAT=✗" in out
    assert "codes  : 1,2,6,11" in out           # raw codes shown (the source of truth for a model diff)
    # NOTE: we do NOT flag fan/auto climate — the B10 has the fan yet declares no CLIMATE_ADVANCED(17).


def test_abilities_section_present_comfort(monkeypatch):
    # A B10-like set: heated + ventilated seats + heated steering all declared.
    monkeypatch.setattr(db_reader, "get_vehicle",
                        lambda: ({"abilities": json.dumps([6, 14, 15, 21, 42, 43])}, {}))
    out = diagnostics._abilities_section()
    assert "SEAT_HEAT=✓" in out
    assert "SEAT_VENT_DRV=✓" in out and "SEAT_VENT_PAS=✓" in out
    assert "STEERING_HEAT=✓" in out


def test_abilities_section_when_not_reported(monkeypatch):
    monkeypatch.setattr(db_reader, "get_vehicle", lambda: ({"abilities": None}, {}))
    assert "not reported yet" in diagnostics._abilities_section()


def test_abilities_section_lists_unmapped_codes(monkeypatch):
    # Codes the car declares but the enum can't name yet (newer than it) get their own line so
    # they pop out as leads.
    monkeypatch.setattr(db_reader, "get_vehicle",
                        lambda: ({"abilities": json.dumps([6, 999])}, {}))   # 6=AC_ON known, 999 unknown
    out = diagnostics._abilities_section()
    assert "unmapped: 999" in out                       # the unknown code is surfaced on its own line


def test_abilities_section_unmapped_none_when_all_known(monkeypatch):
    monkeypatch.setattr(db_reader, "get_vehicle",
                        lambda: ({"abilities": json.dumps([6, 14])}, {}))     # both known
    assert "unmapped: (none)" in diagnostics._abilities_section()


# ── what the ACCOUNT may do (#400) ────────────────────────────────────────────────────────────────
# A command needs the account's right beside the car's ability. The bundle carried the abilities
# only, so a command the contract refused could not be told from a right the account lacks.
_VIN = "LFZTEST0000000001"


def _listed(**vehicle):
    return json.dumps({"account": "0f" * 32, "at": 0, "shared": True,
                       "vehicle": {"vin": _VIN, "carType": "T03", **vehicle}})


def test_abilities_section_shows_the_account_rights(monkeypatch):
    monkeypatch.setattr(db_reader, "get_vehicle", lambda: (
        {"vin": _VIN, "abilities": json.dumps([6, 11, 36])},
        {"api_v2_access_" + _VIN.lower(): _listed(rightList="230,110,120,170",
                                                  moduleRights="100,200")}))
    out = diagnostics._abilities_section()
    assert "codes  : 6,11,36" in out
    assert "rights : 110,120,170,230" in out
    assert "modules: 100,200" in out
    assert "shared : yes" in out
    assert "0f0f" not in out, "the account's hash stays out of the bundle"


def test_the_rights_show_before_the_car_reports_its_abilities(monkeypatch):
    monkeypatch.setattr(db_reader, "get_vehicle", lambda: (
        {"vin": _VIN, "abilities": None},
        {"api_v2_access_" + _VIN.lower(): _listed(rightList="110", moduleRights="")}))
    out = diagnostics._abilities_section()
    assert "not reported yet" in out
    assert "rights : 110" in out and "modules: (none)" in out


def test_a_car_the_vehicle_list_has_not_given_says_so(monkeypatch):
    monkeypatch.setattr(db_reader, "get_vehicle", lambda: (
        {"vin": _VIN, "abilities": json.dumps([6])}, {}))
    assert "rights : (not listed yet" in diagnostics._abilities_section()
