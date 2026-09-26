"""Offline B10 payloads limited to combinations physically confirmed in trials.

This is not an executor, capability check, PIN handler or execution permit.
"""
import json
from dataclasses import dataclass
from .errors import ValidationError


@dataclass(frozen=True, slots=True)
class B10Payload:
    command: str
    state: str
    evidence: str = 'b10_prior_physical_trial_not_current_authorization'


def prepare_b10(action, *, model, value=None, position=None):
    if model != 'B10':
        raise ValidationError('Only B10 payloads are validated')
    if position is not None and action not in ('seat_heat','seat_ventilation'):
        raise ValidationError('Unexpected position')
    if action == 'windows':
        if type(value) is not int or value not in (0,20,50,100):
            raise ValidationError('Untested window percentage')
        command, state = '230', {'value':str(value//10)}
    elif action == 'doors':
        if value not in ('lock','unlock'):
            raise ValidationError('Expected lock or unlock')
        command, state = '110', {'value':value}
    elif action == 'trunk':
        if type(value) is not bool:
            raise ValidationError('Expected explicit trunk state')
        command, state = '130', {'value':'true' if value else 'false'}
    elif action in ('seat_heat','seat_ventilation'):
        if position not in ('left_front','right_front') or type(value) is not int or value not in (0,1):
            raise ValidationError('Untested seat combination')
        command = '301' if action == 'seat_heat' else '370'
        state = {'position':position,'level':str(value)}
    elif action == 'wheel_heat':
        if type(value) is not bool:
            raise ValidationError('Expected explicit wheel-heating state')
        command, state = '320', {'level':'2' if value else '1'}
    elif action == 'climate':
        modes = {'auto22':('auto','nohotcold','22'),
                 'cold22':('manual','cold','22'),
                 'hot26':('manual','hot','26'),
                 'ventilation26':('manual','nohotcold','26')}
        command = '170'
        if value == 'off':
            state = {'operate':'off'}
        elif isinstance(value,str) and value in modes:
            operate, mode, temperature = modes[value]
            state = dict(circle='in',mode=mode,operate=operate,position='all',
                         temperature=temperature,windlevel='3',wshld='0')
        else:
            raise ValidationError('Untested climate combination')
    else:
        raise ValidationError('Unverified action')
    return B10Payload(command,json.dumps(state,separators=(',',':')))
