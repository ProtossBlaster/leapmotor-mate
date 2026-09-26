"""Experimental protocol core. Authentication and commands are explicit opt-ins."""

from .capabilities import evaluate
from .catalog import CatalogEntry, load_reference_catalog
from .commands import PreparedCommand, prepare_seat_ventilation
from .errors import CommandUnavailable, ValidationError
from .models import Availability, CapabilitySnapshot, Decision, VehicleIdentity
from .signing import derive_v2_key, sign_authenticated, sign_login

__all__ = [
    "Availability", "CapabilitySnapshot", "CatalogEntry", "CommandUnavailable",
    "Decision", "PreparedCommand", "ValidationError", "VehicleIdentity",
    "derive_v2_key", "evaluate", "load_reference_catalog", "prepare_seat_ventilation",
    "sign_authenticated", "sign_login",
]
