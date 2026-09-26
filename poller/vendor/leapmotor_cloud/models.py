"""Immutable per-vehicle inputs; raw capability numbers remain lossless."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .errors import ValidationError


def require_aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("An aware datetime is required")


@dataclass(frozen=True, slots=True)
class VehicleIdentity:
    vin: str = field(repr=False)
    model: str
    configuration: str | None = None

    def __post_init__(self):
        for value in (self.vin, self.model):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValidationError("Invalid vehicle identity")
        if self.configuration is not None and (
            not isinstance(self.configuration, str) or not self.configuration.strip()
            or len(self.configuration) > 256
        ):
            raise ValidationError("Invalid vehicle configuration")


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    vehicle: VehicleIdentity
    abilities: frozenset[int]
    rights: frozenset[int]
    module_rights: frozenset[int]
    owner: bool | None
    complete: bool
    observed_at: datetime
    rights_present: bool = True
    module_rights_present: bool = True

    def __post_init__(self):
        if not isinstance(self.vehicle, VehicleIdentity):
            raise ValidationError("Invalid vehicle identity")
        for codes in (self.abilities, self.rights, self.module_rights):
            if not isinstance(codes, frozenset) or any(type(c) is not int or c <= 0 for c in codes):
                raise ValidationError("Capability codes must be immutable positive integers")
        if (self.owner is not None and type(self.owner) is not bool) or type(self.complete) is not bool:
            raise ValidationError("Invalid snapshot flags")
        require_aware(self.observed_at)
        if type(self.rights_present) is not bool or type(self.module_rights_present) is not bool:
            raise ValidationError("Permission presence must be explicit booleans")
        if (not self.rights_present and self.rights) or (not self.module_rights_present and self.module_rights):
            raise ValidationError("Absent permission fields cannot contain values")


class Availability(str, Enum):
    AVAILABLE = "available"
    UNSUPPORTED = "unsupported"
    FORBIDDEN = "forbidden"
    UNKNOWN = "unknown"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


@dataclass(frozen=True, slots=True)
class Decision:
    state: Availability
    reason: str
