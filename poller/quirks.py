"""What a car's frame is known to get wrong, by the car's identity.

The pattern is the kernel's quirk table: one row per identity with the evidence beside it, matched
once per car, and a fixup applied to the frame at one point, right after it is parsed, so that
every reader of the frame sees a reading the car can vouch for. A row is added on proof from that
car's own frames, never on a guess, and a car that matches no row has no quirks.
"""
import dataclasses

# The pack current (signal 1178) reads ~0 A through a whole AC charge: the on-board charger feeds
# the pack past that sensor. Charge detection covers it by signature (client._is_charging, v2.8.4);
# this flag is for the reading itself, which while the cable is in is not the charge.
PACK_CURRENT_BLIND_TO_AC_CHARGE = "pack_current_blind_to_ac_charge"

_NONE = frozenset()

# identity → flags
_TABLE = {
    ("C10", True): frozenset({PACK_CURRENT_BLIND_TO_AC_CHARGE}),   # beta #13, one car; v2.8.4
}


def _identity(vehicle, data) -> tuple:
    """What a row is matched on: the model as the cloud names it, and whether the frame reports a
    fuel tank — the C10 exists as both, and the evidence is for the range extender."""
    return ((getattr(vehicle, "car_type", "") or "").upper(), bool(data.is_reev))


def for_car(vehicle, data) -> frozenset:
    """The quirks of the car this frame came from."""
    return _TABLE.get(_identity(vehicle, data), _NONE)


def fix_frame(data, vehicle):
    """The frame as the car should have reported it: a reading a quirk names, and whatever was
    derived from it, become None."""
    if PACK_CURRENT_BLIND_TO_AC_CHARGE in for_car(vehicle, data):
        # plugged, not charging: this car's cable state flickers 2 → 1 → 3 → 2 mid-charge and charge
        # detection follows it, while the on-board charger keeps feeding the pack. The DC gun and
        # V2L go through the pack, so their current is real
        if data.plug_connected and not data.dc_gun_connected and not data.v2l_active:
            data = dataclasses.replace(data, charge_current_a=None, charge_power_kw=None)
    return data
