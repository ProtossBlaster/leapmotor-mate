"""Confirmed capability labels only; unknown identifiers remain CODE_<id>."""
from enum import IntEnum


class VehicleAbility(IntEnum):
    AC_ON = 6
    LOCK_UNLOCK = 10
    FIND_CAR = 11
    AC_PRESET = 9
    UNLOCK_CHARGE_GUN = 48
    NAVIGATION = 52
    CHARGE_LIMIT = 35
    REAR_HEAT = 19
    SEAT_HEAT = 14
    STEERING_HEAT = 15
    FRONT_SEAT_HEAT = 21
    SEAT_VENT_DRV = 42
    SEAT_VENT_PAS = 43
