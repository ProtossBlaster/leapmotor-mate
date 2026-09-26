"""Mate SDK exception compatibility; contracts live in the independent package."""
from functools import wraps
from leapmotor_cloud.mate_compat import MateAPIError as LeapmotorApiError
from leapmotor_cloud import command_contracts as _core
from leapmotor_cloud.errors import ValidationError

APPOINTMENT_PATH = _core.APPOINTMENT_PATH
COMMAND_RULES = _core.COMMAND_RULES
UNAVAILABLE = _core.UNAVAILABLE
AIR_KEYS = _core.AIR_KEYS
CHARGE_KEYS = _core.CHARGE_KEYS


def _compat(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValidationError as exc:
            raise LeapmotorApiError(str(exc)) from None
    return call


fail = _compat(_core.fail)
require = _compat(_core.require)
finite = _compat(_core.finite)
fields = _compat(_core.fields)
text = _compat(_core.text)
air = _compat(_core.air)
navigation = _compat(_core.navigation)
charge = _compat(_core.charge)
seat_position = _compat(_core.seat_position)
bundle = _compat(_core.bundle)
appointment = _compat(_core.appointment)
prepare = _compat(_core.prepare)
