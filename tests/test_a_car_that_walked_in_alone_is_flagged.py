"""A car nobody configured must say so — the banner Silvio decided on 13/08/2026.

A second car that arrives on a Mate which has been running for months never meets the wizard: the
login is already done, so the poller registers it and it silently takes **its model's default
pack**. On a C10 that default is the RWD: an AWD then runs on a 20% wrong pack and a REEV on one
**2.4 times** wrong — and its kWh, €/kWh and consumption figures are quietly bent, with nothing on
screen saying why. The wizard has been stamping `vehicle_setup_done_<vin>` since v3.13.0 and
**nobody has ever read it**.

🔴 The trap this file exists for: **absent ≠ unconfigured.** No install older than v3.13.0 carries
that stamp on ANY car — including the thousands with a single, perfectly configured one. A banner
lit on "the stamp is missing" would appear on every screen out there. So the poller stamps, once,
the cars that were **already there** at the moment of the update; only a car that walks in later is
uncovered. Same shape as the three migrations in v3.13.0, and idempotent.

And the mirror of it, which is why the web has a guard of its own: until that one-time stamping has
actually run, the web knows **nothing** and must claim nothing — a page rendered between the update
and the poller's first start would otherwise accuse every car on the install.
"""
import pytest


def _pre_3_13_install(tmp_path, monkeypatch, *, cars=("B10",)):
    """A database as it looked before v3.13.0: cars registered, not one stamp anywhere."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    for i, car_type in enumerate(cars, start=1):
        pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (?,?,?)",
                          (i, f"LFZTEST000000000{i}", car_type))
    pdb._conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    # The one-time stamping has never run here: this IS the pre-update state.
    pdb._conn.execute("DELETE FROM settings WHERE key = 'vehicle_setup_backfilled'")
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _start_the_poller(path):
    """Opening the Database is what runs the migration — the poller owns the data."""
    import db as D
    pdb = D.Database(path)
    pdb._conn.close()


def _flagged():
    import db_reader
    return [v["car_type"] for v in db_reader.unconfigured_vehicles()]


# ── the update ────────────────────────────────────────────────────────────────
def test_an_update_does_not_accuse_the_cars_that_were_already_there(tmp_path, monkeypatch):
    """The whole install, banner-free, exactly as it was the minute before the update."""
    path = _pre_3_13_install(tmp_path, monkeypatch, cars=("B10", "C10"))
    _start_the_poller(path)
    assert _flagged() == []


def test_the_web_claims_nothing_until_the_stamping_has_run(tmp_path, monkeypatch):
    """Page rendered after the update but before the poller started: no stamps, no migration, and
    therefore no knowledge. Silence is the only honest answer — the alternative accuses everyone."""
    _pre_3_13_install(tmp_path, monkeypatch, cars=("B10", "C10"))
    assert _flagged() == []


# ── the car that walks in on its own ──────────────────────────────────────────
def test_a_car_that_arrives_after_the_update_is_the_only_one_flagged(tmp_path, monkeypatch):
    """The case the banner exists for: months later, a second car appears from the poller."""
    import db as D
    path = _pre_3_13_install(tmp_path, monkeypatch, cars=("B10",))
    _start_the_poller(path)
    pdb = D.Database(path)
    pdb.ensure_vehicle("LFZTEST0000000099", "C10")     # nobody asked this one anything
    pdb._conn.close()
    assert _flagged() == ["C10"]


def test_starting_the_poller_again_does_not_adopt_the_new_car(tmp_path, monkeypatch):
    """The stamping must happen ONCE. Run at every start, it would stamp the newcomer on the next
    restart and the banner would vanish before anyone had answered anything."""
    import db as D
    path = _pre_3_13_install(tmp_path, monkeypatch, cars=("B10",))
    _start_the_poller(path)
    pdb = D.Database(path)
    pdb.ensure_vehicle("LFZTEST0000000099", "C10")
    pdb._conn.close()
    _start_the_poller(path)                            # a restart, an update, a reboot
    assert _flagged() == ["C10"]


def test_a_car_the_wizard_configured_is_never_flagged(tmp_path, monkeypatch):
    import db as D
    import db_reader
    path = _pre_3_13_install(tmp_path, monkeypatch, cars=("B10",))
    _start_the_poller(path)
    pdb = D.Database(path)
    pdb.ensure_vehicle("LFZTEST0000000099", "C10")
    pdb._conn.close()
    db_reader.set_setting("vehicle_setup_done_lfztest0000000099", "1")   # the wizard's own stamp
    assert _flagged() == []


def test_nothing_is_flagged_before_the_install_is_set_up(tmp_path, monkeypatch):
    """A fresh install is not an install with an unconfigured car: the wizard is the flow, and a
    banner over it would be noise on top of the very page that answers it."""
    import db as D
    import db_reader
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb.ensure_vehicle("LFZTEST0000000001", "B10")
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    assert db_reader.get_setting("setup_complete", "") != "1"
    assert _flagged() == []


# ── the banner itself ─────────────────────────────────────────────────────────
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _page(tmp_path, monkeypatch, *, newcomer):
    """A whole page, through the real layout: the banner has to be on EVERY page, which is a claim
    about base.html, not about one route."""
    import asyncio

    import db as D
    import db_reader

    path = _pre_3_13_install(tmp_path, monkeypatch, cars=("B10",))
    _start_the_poller(path)
    if newcomer:
        pdb = D.Database(path)
        pdb.ensure_vehicle("LFZTEST0000000099", "C10")
        pdb._conn.close()

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    return asyncio.run(main.settings_page(_Req())).body.decode()


def test_the_banner_names_the_car_and_offers_the_wizard(tmp_path, monkeypatch):
    body = _page(tmp_path, monkeypatch, newcomer=True)
    assert "unconfigured-bar" in body, "no banner on the page"
    bar = body[body.index("unconfigured-bar"):][:1200]
    assert "C10" in bar, bar[:400]
    assert "/setup" in bar, "the banner does not lead anywhere"


def test_no_banner_when_every_car_has_been_seen(tmp_path, monkeypatch):
    assert "unconfigured-bar" not in _page(tmp_path, monkeypatch, newcomer=False)
