"""Per-model capability gating (#67 follow-up): a T03 must see only what it actually has. It has no
ventilated seats (ability 42/43 absent) and no PREPARE right (38) → its owner should NOT be shown the
seat-vent comfort tiles nor the "Prepare car" page/nav link. Driven by capability_profile.MODEL_ABSENT
so new EVs (B05, …) are one line away. HARD CONSTRAINTS proven here:
  • CLIMATE is never gated from this table (its T03 ability codes lie — see #67).
  • Other models (B10/C10/B05) and callers that pass no car_type are byte-for-byte unchanged.
CI-safe: pure helpers + _comfort_rows with db_reader.get_setting stubbed (no DB, no network)."""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
import capability_profile as cp
import main


# ── The pure gate: MODEL_ABSENT / model_hidden ────────────────────────────────
def test_t03_hides_exactly_seatvent_and_prepare():
    assert cp.model_hidden("T03", "seat_vent")
    assert cp.model_hidden("T03", "seat_vent_cmd")
    assert cp.model_hidden("T03", "prepare_car")
    # Climate must NEVER be in the table — the T03's climate abilities are misleading (#67).
    for climate_feat in ("ac_state", "ac_target", "climate_off", "defrost", "recirc"):
        assert not cp.model_hidden("T03", climate_feat), f"{climate_feat} must not be model-gated"


def test_other_models_hide_nothing():
    # B10/C10 fully featured; B05 deliberately NOT listed yet (characterise on-car first).
    for ct in ("B10", "C10", "B05", "", "UNKNOWN"):
        for feat in ("seat_vent", "seat_vent_cmd", "prepare_car", "seat_heat"):
            assert not cp.model_hidden(ct, feat), f"{ct}/{feat} should be shown"


def test_model_hidden_is_case_insensitive():
    assert cp.model_hidden("t03", "seat_vent") == cp.model_hidden("T03", "seat_vent") is True


# ── is_shown honours the model gate only when car_type is supplied ─────────────
def test_is_shown_gates_absent_comfort_on_t03():
    assert cp.is_shown("VIN", "seat_vent", car_type="T03") is False
    assert cp.is_shown("VIN", "seat_vent", car_type="B10") is True
    # The EU T03 has NO heated seats and NO heated steering wheel either — verified against the official
    # Stellantis spec (single European trim, only heated mirrors) — so they're gated too, despite the
    # firmware DECLARING the abilities (a shared-platform lie, like climate #67). #144.
    assert cp.is_shown("VIN", "seat_heat", car_type="T03") is False
    assert cp.is_shown("VIN", "steering_heat", car_type="T03") is False
    # …but a B10, which really has them, keeps them.
    assert cp.is_shown("VIN", "seat_heat", car_type="B10") is True


def test_is_shown_without_cartype_is_unchanged():
    # Backward compatibility: every existing caller passes no car_type → nothing new is hidden.
    assert cp.is_shown("VIN", "seat_vent") is True
    assert cp.is_shown("VIN", "prepare_car") is True


def test_core_features_never_hidden_even_if_listed(monkeypatch):
    # A CORE feature stays shown regardless — model gate must not override the core guard.
    monkeypatch.setitem(cp.MODEL_ABSENT, "T03", cp.MODEL_ABSENT["T03"] + ("soc",))
    assert cp.is_shown("VIN", "soc", car_type="T03") is True


# ── Integration: the Commands comfort rows drop seat-vent on a T03 ─────────────
def _no_settings(monkeypatch):
    # comfort_state_<vin> and capabilities_<vin> both empty → states default 0, no 'broken' verdicts.
    monkeypatch.setattr(main.db_reader, "get_setting", lambda k, d="": "")


def test_comfort_rows_drop_absent_comfort_on_t03(monkeypatch):
    _no_settings(monkeypatch)
    skeys = {r["skey"] for r in main._comfort_rows("VIN123", "T03")}
    # The EU T03 lacks ventilated AND heated seats AND a heated steering wheel (#144) → all gated out.
    assert not ({"seat_vent_driver", "seat_vent_passenger",
                 "seat_heat_driver", "seat_heat_passenger", "steering_heat"} & skeys)


def test_comfort_rows_keep_seatvent_on_b10(monkeypatch):
    _no_settings(monkeypatch)
    skeys = {r["skey"] for r in main._comfort_rows("VIN123", "B10")}
    assert {"seat_vent_driver", "seat_vent_passenger"} <= skeys


def test_comfort_rows_default_cartype_keeps_everything(monkeypatch):
    # No car_type passed (defensive default) → nothing model-gated, all 7 rows present.
    _no_settings(monkeypatch)
    skeys = {r["skey"] for r in main._comfort_rows("VIN123")}
    assert "seat_vent_driver" in skeys
