"""Offline legacy payload simulation. Deliberately no execute operation."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .capabilities import evaluate
from .errors import CommandUnavailable, ValidationError
from .models import Availability, CapabilitySnapshot, Decision


@dataclass(frozen=True, slots=True)
class PreparedCommand:
    action: str
    cmd_id: str
    content: str
    decision: Decision
    evidence: str = field(default="legacy_payload_simulation_only", init=False)


def prepare_seat_ventilation(snapshot: CapabilitySnapshot, *, position: int, level: int,
                             now: datetime, max_age: timedelta) -> PreparedCommand:
    """Simulate legacy position 1 only, never send it to a vehicle.

    Position indices on the new command API have not been verified. A passing
    capability decision is necessary for this simulation, not proof of support
    for real execution, current vehicle state or a 2.0 command contract.
    """
    if type(position) is not int or position != 1:
        raise ValidationError("Only legacy driver position 1 is reconstructed for simulation")
    if type(level) is not int or not 0 <= level <= 3:
        raise ValidationError("Ventilation level must be an integer between 0 and 3")
    decision = evaluate(snapshot, ability=42, right=370, now=now, max_age=max_age)
    if decision.state is not Availability.AVAILABLE:
        raise CommandUnavailable(decision)
    content = json.dumps({"value": f"{position},{level}"}, separators=(",", ":"))
    return PreparedCommand("seat_ventilation", "370", content, decision)

