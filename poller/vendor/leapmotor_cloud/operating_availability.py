"""Conservative B10 front-seat ventilation gate, not permission to execute."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .capabilities import evaluate
from .errors import ValidationError
from .models import Availability as A, CapabilitySnapshot, Decision, require_aware


@dataclass(frozen=True, slots=True)
class OperatingState:
    vin: str = field(repr=False)
    observed_at: datetime | None
    driving: bool | None
    on3: bool | None

    def __post_init__(self):
        if not isinstance(self.vin, str) or not self.vin:
            raise ValidationError("Expected operating-state identity")
        if self.observed_at is not None:
            require_aware(self.observed_at)
        if any(v is not None and type(v) is not bool for v in (self.driving, self.on3)):
            raise ValidationError("Operating flags must be explicit booleans")


def operating_decision(state, *, now, state_max_age, allow_stale_parked=False):
    require_aware(now)
    if not isinstance(state,OperatingState) or type(allow_stale_parked) is not bool:
        raise ValidationError('Explicit operating policy required')
    if not isinstance(state_max_age,timedelta) or state_max_age<=timedelta(0):
        raise ValidationError('Positive state freshness limit required')
    if state.observed_at is None:return Decision(A.UNKNOWN,'state_timestamp_missing')
    age=now-state.observed_at
    if age<timedelta(0) or state.observed_at.timestamp()<=0:
        return Decision(A.UNKNOWN,'state_timestamp_invalid')
    if state.driving is True or state.on3 is True:
        return Decision(A.TEMPORARILY_UNAVAILABLE,'vehicle_operating_state_blocks_command')
    if state.driving is None or state.on3 is None:
        return Decision(A.UNKNOWN,'operating_state_incomplete')
    if age>state_max_age:
        if not allow_stale_parked:return Decision(A.UNKNOWN,'state_not_fresh')
        return Decision(A.AVAILABLE,'last_known_parked_not_current_state_confirmation')
    return Decision(A.AVAILABLE,'stationary_on3_off_not_execution_confirmation')


def front_seat_ventilation_availability(snapshot, state, *, position, rudder,
                                      now, capability_max_age, state_max_age, allow_stale_parked=False):
    require_aware(now)
    if not isinstance(snapshot, CapabilitySnapshot) or not isinstance(state, OperatingState):
        raise ValidationError("Expected capability and operating snapshots")
    if position not in ('left_front', 'right_front'):
        raise ValidationError("Only front physical positions are reconstructed")
    if not isinstance(state_max_age, timedelta) or state_max_age <= timedelta(0):
        raise ValidationError("A positive state freshness limit is required")
    if state.vin != snapshot.vehicle.vin:
        raise ValidationError("Operating-state identity mismatch")
    if snapshot.vehicle.model != 'B10':
        return Decision(A.UNKNOWN, 'model_contract_unverified')
    if rudder not in ('left', 'right'):
        return Decision(A.UNKNOWN, 'rudder_unknown')
    is_driver = position == ('left_front' if rudder == 'left' else 'right_front')
    decision = evaluate(snapshot, ability=42 if is_driver else 43, right=370,
                        now=now, max_age=capability_max_age)
    if decision.state is not A.AVAILABLE:
        return decision
    return operating_decision(state,now=now,state_max_age=state_max_age,allow_stale_parked=allow_stale_parked)
