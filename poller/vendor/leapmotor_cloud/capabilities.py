"""Conservative capability/permission layer, not a remote-execution permit."""

from datetime import datetime, timedelta

from .errors import ValidationError
from .models import Availability as A, CapabilitySnapshot, Decision, require_aware


def permission_decision(*, model, owner, ability_supported, right_allowed,
                        module_allowed, rights_present=True, module_rights_present=True):
    """Owner must originate from an authenticated binding, never guessed."""
    if owner is not None and type(owner) is not bool:
        raise ValidationError('Invalid ownership flag')
    if any(type(x) is not bool for x in (ability_supported,right_allowed,module_allowed,
                                        rights_present,module_rights_present)):
        raise ValidationError('Explicit permission flags required')
    if not ability_supported:return Decision(A.UNSUPPORTED,'ability_absent')
    if owner is None:return Decision(A.UNKNOWN,'ownership_unknown')
    owner_omission=owner and model=='B10'
    if not module_rights_present:
        if not owner_omission:return Decision(A.FORBIDDEN,'control_module_missing')
    elif not module_allowed:return Decision(A.FORBIDDEN,'control_module_denied')
    if not rights_present:
        if not owner_omission:return Decision(A.FORBIDDEN,'account_right_missing')
    elif not right_allowed:return Decision(A.FORBIDDEN,'account_right_denied')
    return Decision(A.AVAILABLE,'explicit_ability_and_permission')


def evaluate(snapshot: CapabilitySnapshot, *, ability: int, right: int,
             now: datetime, max_age: timedelta) -> Decision:
    if not isinstance(snapshot, CapabilitySnapshot):
        raise ValidationError("Invalid capability snapshot")
    if any(type(code) is not int or code <= 0 for code in (ability, right)):
        raise ValidationError("Invalid capability requirement")
    require_aware(now)
    if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
        raise ValidationError("A positive freshness limit is required")
    age = now - snapshot.observed_at
    if age < timedelta(0):
        return Decision(A.UNKNOWN, "snapshot_from_future")
    if age > max_age:
        return Decision(A.UNKNOWN, "snapshot_expired")
    if not snapshot.complete:
        return Decision(A.UNKNOWN, "snapshot_incomplete")
    return permission_decision(model=snapshot.vehicle.model,owner=snapshot.owner,
        ability_supported=ability in snapshot.abilities,right_allowed=right in snapshot.rights,
        module_allowed=200 in snapshot.module_rights,rights_present=snapshot.rights_present,
        module_rights_present=snapshot.module_rights_present)
