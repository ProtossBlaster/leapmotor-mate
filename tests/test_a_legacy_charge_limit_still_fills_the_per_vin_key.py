"""The Overview hero reads the charge limit from the per-VIN settings key ONLY (beta #33). So the
poll loop must FILL that per-VIN key from the car — and it did not, on any instance still carrying a
legacy shared `charge_limit_percent` (older versions, or the Set-limit button). Its "changed?" guard
read the limit through a fallback that includes the shared key, so a shared value that matched the
car's setting made it skip the per-VIN write forever.

Seen on Silvio's own prod (v3.14.9) beside a fresh Docker on the SAME version: the hero printed the
% on the fresh DB and NOTHING on the upgraded one — the migration trap, where the fresh case masks
the bug. → [[migration-on-the-alter-misses-who-already-updated]]
"""
import importlib.util
import pathlib
import sys

import db as D


def _poller_main():
    """poller/main.py under its own name — a bare `import main` gets web/main.py.
    → [[mate-two-main-py-collision]]"""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_persist", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["poller_main_persist"] = mod
    spec.loader.exec_module(mod)
    return mod


PM = _poller_main()
VIN = "LFZTEST0000000001"


def _db(tmp_path):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'B10')", (VIN,))
    pdb._conn.commit()
    return pdb


def test_a_legacy_shared_limit_does_not_block_the_per_vin_write(tmp_path):
    """Silvio's prod: a shared '90' from an older poller, the car still reporting 90. The per-VIN key
    the hero reads must be written all the same."""
    pdb = _db(tmp_path)
    pdb.set_setting("charge_limit_percent", "90")          # the legacy shared key
    PM._persist_charge_limit(pdb, VIN, 90)
    assert pdb.get_setting(f"charge_limit_percent_{VIN.lower()}", "") == "90"


def test_a_fresh_instance_still_writes_it(tmp_path):
    pdb = _db(tmp_path)                                     # no legacy key at all
    PM._persist_charge_limit(pdb, VIN, 90)
    assert pdb.get_setting(f"charge_limit_percent_{VIN.lower()}", "") == "90"


def test_a_car_that_reports_no_limit_writes_nothing(tmp_path):
    pdb = _db(tmp_path)
    PM._persist_charge_limit(pdb, VIN, None)
    assert pdb.get_setting(f"charge_limit_percent_{VIN.lower()}", "") == ""
