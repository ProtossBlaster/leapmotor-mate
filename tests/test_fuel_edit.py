"""Editing a refuel after the fact — beta discussion #34, @pdifeo.

"Is there a way to change the price per litre when filling up, or even the other details?"

Until now the answer was delete-and-retype. The price is the one field the cloud can never know
and the one field a pump receipt corrects (a discount applied after the fact, a total that includes
a hot-dog) — so a refuel needed the same in-place correction every other record already has.

The server-side contract mirrors add_fuel_purchase exactly: when both price fields arrive, €/L wins
and derives the total; a total alone derives the €/L; fields left blank keep their stored value.
fuel_before_pct — the WAC's residual weight — moves only when the instant itself does: correcting
a typo in the price must not rewrite the blend's history.
"""
import sqlite3

import db_reader


def _setup_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE vehicles (id INTEGER PRIMARY KEY, vin TEXT, car_type TEXT)")
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'VINX', 'C10 REEV')")
    con.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "vehicle_id INTEGER, recorded_at TEXT, fuel_level_pct REAL)")
    con.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, fuel_level_pct) VALUES (?,?,?)",
        [(1, "2026-07-10T08:00:00+00:00", 80.0),
         (1, "2026-07-12T08:00:00+00:00", 16.0)])
    con.commit()
    con.close()


def _row(dbp, pid):
    import sqlite3 as s
    con = s.connect(dbp)
    con.row_factory = s.Row
    r = con.execute("SELECT * FROM fuel_purchases WHERE id=?", (pid,)).fetchone()
    con.close()
    return dict(r) if r else None


# ── price edits ───────────────────────────────────────────────────────────────

def test_a_new_price_per_litre_derives_the_total(tmp_path, monkeypatch):
    """@pdifeo's exact ask: the pump charged 1.699, Mate had 1.75."""
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=34.5, price_per_l=1.75)
    assert db_reader.update_fuel_purchase(pid, price_per_l=1.699)
    r = _row(dbp, pid)
    assert abs(r["price_per_l"] - 1.699) < 1e-4
    assert abs(r["total_cost"] - round(1.699 * 34.5, 2)) < 1e-6


def test_a_new_total_alone_derives_the_price_per_litre(tmp_path, monkeypatch):
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=20, price_per_l=1.70)
    assert db_reader.update_fuel_purchase(pid, total_cost=40.00)
    r = _row(dbp, pid)
    assert abs(r["price_per_l"] - 2.0) < 1e-4
    assert abs(r["total_cost"] - 40.0) < 1e-6


def test_both_prices_arriving_means_eur_per_l_wins_like_the_add_form(tmp_path, monkeypatch):
    """The edit form posts both fields; the pair must come out consistent, never half-merged."""
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=10, price_per_l=1.50)
    assert db_reader.update_fuel_purchase(pid, price_per_l=1.80, total_cost=99.0)
    r = _row(dbp, pid)
    assert abs(r["price_per_l"] - 1.8) < 1e-4
    assert abs(r["total_cost"] - 18.0) < 1e-6


def test_blank_price_fields_keep_what_is_stored(tmp_path, monkeypatch):
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=10, price_per_l=1.50)
    assert db_reader.update_fuel_purchase(pid, note="Autostrada")
    r = _row(dbp, pid)
    assert abs(r["price_per_l"] - 1.5) < 1e-6 and abs(r["total_cost"] - 15.0) < 1e-6


def test_changing_litres_rederives_from_the_stored_price(tmp_path, monkeypatch):
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=10, price_per_l=2.0)
    assert db_reader.update_fuel_purchase(pid, liters=30)
    r = _row(dbp, pid)
    assert abs(r["total_cost"] - 60.0) < 1e-6          # 30 × 2.00 re-derived
    assert abs(r["price_per_l"] - 2.0) < 1e-9          # untouched


# ── note + guards ────────────────────────────────────────────────────────────

def test_an_empty_string_clears_the_note_but_none_keeps_it(tmp_path, monkeypatch):
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=10,
                                      price_per_l=1.5, note="Via Roma")
    db_reader.update_fuel_purchase(pid, price_per_l=1.6)      # no note kwarg → untouched
    assert _row(dbp, pid)["note"] == "Via Roma"
    db_reader.update_fuel_purchase(pid, price_per_l=1.7, note="")
    assert _row(dbp, pid)["note"] in (None, "")


def test_bad_numbers_are_refused_and_touch_nothing(tmp_path, monkeypatch):
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=10, price_per_l=1.5)
    for bad in ({"liters": -3}, {"price_per_l": 0}, {"total_cost": -1}):
        try:
            db_reader.update_fuel_purchase(pid, **bad)
            assert False, f"expected ValueError for {bad}"
        except ValueError:
            pass
    r = _row(dbp, pid)
    assert abs(r["price_per_l"] - 1.5) < 1e-6 and r["liters"] == 10.0


def test_an_unknown_id_says_so(tmp_path, monkeypatch):
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    assert db_reader.update_fuel_purchase(9999, note="ghost") is False


# ── the timestamp ────────────────────────────────────────────────────────────

def test_moving_the_instant_refreshes_the_residual_snapshot(tmp_path, monkeypatch):
    """A refuel moved to just before the 16 % reading snapshots 16 % as its WAC weight."""
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=10, price_per_l=1.5)
    assert _row(dbp, pid)["fuel_before_pct"] == 80.0
    assert db_reader.update_fuel_purchase(pid, ts="2026-07-12T08:30:00+00:00")
    r = _row(dbp, pid)
    assert abs(r["fuel_before_pct"] - 16.0) < 1e-6
    assert r["ts"] == "2026-07-12T08:30:00+00:00"


def test_a_price_edit_never_rewrites_the_snapshot(tmp_path, monkeypatch):
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=10, price_per_l=1.5)
    before = _row(dbp, pid)["fuel_before_pct"]
    db_reader.update_fuel_purchase(pid, price_per_l=1.9)
    assert _row(dbp, pid)["fuel_before_pct"] == before


# ── the read side ────────────────────────────────────────────────────────────

def test_get_fuel_purchase_returns_the_row_for_the_form(tmp_path, monkeypatch):
    dbp = str(tmp_path / "t.db"); _setup_db(dbp); monkeypatch.setattr(db_reader, "DB_PATH", dbp)
    pid = db_reader.add_fuel_purchase("2026-07-10T09:00:00+00:00", liters=10,
                                      price_per_l=1.5, note="n")
    p = db_reader.get_fuel_purchase(pid)
    assert p["id"] == pid and p["liters"] == 10.0 and p["note"] == "n"
    assert db_reader.get_fuel_purchase(424242) is None


# ── the templates ────────────────────────────────────────────────────────────
# Rendered the same way the suite renders every other partial: a bare Jinja2 environment with the
# project's three money filters stubbed, no HTTP, no DB — the markup is what is under test here.

import json
import pathlib
import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))

_PURCHASE = {"id": 7, "ts": "2026-07-10T09:00:00+00:00", "liters": 34.5,
             "price_per_l": 1.75, "total_cost": 60.38, "note": "Via Roma",
             "fuel_before_pct": 80.0, "vehicle_id": 1}


def _env():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["nice"] = lambda v: f"{v:.1f}"
    env.filters["price3"] = lambda v: f"{v:.3f}"
    env.filters["money"] = lambda v: f"{v:.2f}"
    return env


def _tr(lang="en"):
    for p in LOCALES:
        if p.stem == lang:
            return json.loads(p.read_text())["translations"]
    return json.loads(LOCALES[0].read_text())["translations"]


def _render_row(lang="en"):
    tr = _tr(lang)
    return _env().get_template("partials/fuel_row.html").render(
        p=dict(_PURCHASE), t=lambda k: tr.get(k, k), currency={"symbol": "€"})


def _render_edit(lang="en"):
    tr = _tr(lang)
    p = dict(_PURCHASE)
    p["ts_local_input"] = "2026-07-10T11:00"
    return _env().get_template("partials/fuel_edit_row.html").render(
        purchase=p, t=lambda k: tr.get(k, k), currency={"symbol": "€"})


def test_the_row_carries_the_edit_button_that_opens_the_form():
    out = _render_row()
    assert 'hx-get="api/fuel/7/edit-form"' in out
    assert 'id="fuel-row-7"' in out                       # the swap anchor the form targets


def test_the_form_posts_to_the_edit_endpoint_prefilled():
    out = _render_edit()
    assert 'hx-post="api/fuel/edit"' in out
    assert 'name="id" value="7"' in out                   # which refuel it edits
    assert 'name="price_per_l"' in out and 'value="1.750"' in out
    assert 'name="total_cost"' in out and 'value="60.38"' in out
    assert 'name="liters"' in out and 'value="34.5"' in out
    assert 'name="note"' in out and 'value="Via Roma"' in out


def test_cancel_swaps_back_to_the_row_without_touching_the_server_list():
    out = _render_edit()
    assert 'hx-get="api/fuel/7/row"' in out
    assert 'hx-target="#fuel-row-7"' in out               # same anchor → clean restore


def test_typing_in_one_price_field_blanks_the_other():
    """Both fields arrive pre-filled; without this the stale €/L would always overrule an edit
    to just the total (server rule: €/L wins when both are posted)."""
    out = _render_edit()
    assert "data-price-pair" in out and "addEventListener('input'" in out


def test_every_language_knows_the_three_new_strings():
    assert len(LOCALES) >= 8, [p.name for p in LOCALES]
    missing = []
    for p in LOCALES:
        tr = json.loads(p.read_text())["translations"]
        for k in ("fuel_edit", "fuel_edit_save", "fuel_edit_cancel"):
            if not tr.get(k):
                missing.append(f"{p.stem}:{k}")
    assert not missing, missing
