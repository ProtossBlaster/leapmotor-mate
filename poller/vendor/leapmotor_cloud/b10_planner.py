"""Offline command planner. No sender, PIN material or execution authority."""
from dataclasses import dataclass
from datetime import timedelta
from .b10_payloads import prepare_b10, B10Payload
from .capabilities import evaluate
from .errors import ValidationError
from .models import Availability as A, CapabilitySnapshot, Decision, require_aware
from .operating_availability import OperatingState, front_seat_ventilation_availability, operating_decision
from .command_contracts import COMMAND_RULES


@dataclass(frozen=True)
class B10Plan:
    decision: Decision
    payload: B10Payload | None
    requires_physical_confirmation: bool = True


def plan_b10(snapshot, state, *, action, now, capability_max_age,
             state_max_age, value=None, position=None, rudder=None, allow_stale_parked=False):
    if not isinstance(snapshot, CapabilitySnapshot) or not isinstance(state, OperatingState):
        raise ValidationError('Expected capability and operating snapshots')
    require_aware(now)
    if state.vin != snapshot.vehicle.vin:
        raise ValidationError('Vehicle identity mismatch')
    if not isinstance(state_max_age, timedelta) or state_max_age <= timedelta(0):
        raise ValidationError('Positive state freshness limit required')
    if snapshot.vehicle.model != 'B10':
        return B10Plan(Decision(A.UNKNOWN,'model_contract_unverified'),None)
    commands = {'doors':'110','trunk':'130','windows':'230',
                'climate':'170','seat_heat':'301','wheel_heat':'320'}
    if action=='seat_ventilation':
        decision=front_seat_ventilation_availability(snapshot,state,position=position,
            rudder=rudder,now=now,capability_max_age=capability_max_age,state_max_age=state_max_age,
            allow_stale_parked=allow_stale_parked)
    elif action in commands:
        right,ability=COMMAND_RULES[commands[action]]
        decision=evaluate(snapshot,ability=ability,right=right,now=now,max_age=capability_max_age)
    else:
        return B10Plan(Decision(A.UNKNOWN,'command_contract_unverified'),None)
    if decision.state is not A.AVAILABLE:
        return B10Plan(decision,None)
    operating=operating_decision(state,now=now,state_max_age=state_max_age,
                                 allow_stale_parked=allow_stale_parked)
    if operating.state is not A.AVAILABLE:return B10Plan(operating,None)
    payload=prepare_b10(action,model='B10',value=value,position=position)
    return B10Plan(operating,payload)
