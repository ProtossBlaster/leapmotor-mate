"""B10 lab contracts from app 1.16.4 static/runtime evidence, not actuation proof.

Command IDs, rights and abilities are independent namespaces. Unknown payloads
fail closed. This module never performs network requests or vehicle commands.
"""
import math
import re
from datetime import datetime, timezone
from .scheduling import local_start
from .models import require_aware
from .models import Availability
from .capabilities import permission_decision
from .errors import ValidationError as CommandContractError

APPOINTMENT_PATH='/carownerservice/v3/api/appremotectl/oversea/appointment'
# cmd: (permission, ability). Passenger ventilation is resolved per position.
COMMAND_RULES={
    '110':(110,10),'120':(120,11),'130':(130,3),'160':(190,10),
    '170':(170,6),'171':(171,9),'180':(180,52),'190':(340,35),
    '192':(192,48),'193':(193,82),'230':(230,12),'240':(161,13),
    '301':(301,21),'320':(320,15),'360':(360,38),'361':(361,38),
    '370':(370,42),'440':(440,19),
}
UNAVAILABLE={'220':'Sentinel: app uses command 400, but its B10 availability and actuation are not validated.',
             '400':'Sentinel is disabled by the examined official-app availability path.'}
AIR_KEYS={'circle','mode','operate','position','temperature','windlevel','wshld'}
CHARGE_KEYS={'chargeEnable','chargesoc','circulation','cycles','endtime','recharge','starttime'}


def fail(message):
    raise CommandContractError('Command not sent: '+message)


def require(vehicle,cmd,ability=None):
    right,default=COMMAND_RULES[cmd]
    raw_vehicle=getattr(vehicle,'raw',{})
    # The authenticated bindcars entry identifies the owner. Unlike sharedcars,
    # it may omit rightList/moduleRights: the official app derives the owner's
    # permissions from abilities. Never apply this exception to a shared or
    # unidentified vehicle, or override an explicitly supplied permission list.
    owner=(getattr(vehicle,'is_shared',None) is False
           and raw_vehicle.get('vin')==vehicle.vin
           and str(raw_vehicle.get('carType','')).upper()=='B10')
    right_allowed=(owner and raw_vehicle.get('rightList') is None) or vehicle.has_right(right)
    module_allowed=(owner and raw_vehicle.get('moduleRights') is None) or vehicle.has_module_right(200)
    ability=default if ability is None else ability
    raw=raw_vehicle.get('abilities')
    if isinstance(raw,list):
        try:
            if any(not (type(v) is int and v>0 or isinstance(v,str) and v.isascii() and v.isdecimal() and int(v)>0) for v in raw):
                raise ValueError()
            supported=ability in {int(v) for v in raw}
        except (ValueError,TypeError):supported=False
    else:supported=vehicle.has_ability(ability)
    decision=permission_decision(model='B10',owner=bool(owner),ability_supported=bool(supported),
        right_allowed=bool(right_allowed),module_allowed=bool(module_allowed),
        rights_present=not(owner and raw_vehicle.get('rightList') is None),
        module_rights_present=not(owner and raw_vehicle.get('moduleRights') is None))
    if decision.state is not Availability.AVAILABLE:fail(decision.reason+' for '+cmd)


def finite(value,low,high):
    try:
        if type(value)is bool:raise ValueError()
        n=float(value)
        if not math.isfinite(n) or not low<=n<=high:raise ValueError()
    except (TypeError,ValueError,OverflowError):fail('numeric value outside the supported range')
    return n


def fields(state,required,optional=frozenset()):
    if not isinstance(state,dict) or not required<=set(state) or set(state)-required-optional:
        fail('unsupported payload fields')


def text(value,limit=512):
    if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 for c in value):
        fail('invalid text field')


def air(state):
    if state=={'operate':'off'}:return state
    fields(state,AIR_KEYS)
    if state['operate']=='close':return {'operate':'off'}
    if (state['operate'] not in ('auto','manual') or state['mode'] not in ('cold','hot','wind','nohotcold')
        or state['circle'] not in ('in','out') or state['position']!='all'
        or state['windlevel'] not in tuple(str(i) for i in range(1,8)) or state['wshld'] not in ('0','1','2')):
        fail('unsupported climate setting')
    finite(state['temperature'],16,32)
    # B10 physical trials: manual/nohotcold, not manual/wind, activates ventilation.
    if state['mode']=='wind':state=dict(state,mode='nohotcold')
    return state


def navigation(state):
    fields(state,{'address','addressname','latitude','longitude','linenum'},{'addresskey','config'})
    for k in ('address','addressname','addresskey','config'):
        if k in state:text(state[k])
    finite(state['latitude'],-90,90);finite(state['longitude'],-180,180)
    if state['linenum']!='0':fail('unsupported navigation line')
    return state


def charge(state):
    fields(state,CHARGE_KEYS)
    for key in ('chargeEnable','circulation','recharge'):
        if type(state[key])is not int or state[key] not in (0,1):fail('invalid charge flag')
    if type(state['chargesoc'])is not int or not 50<=state['chargesoc']<=100:fail('charge target must be 50..100')
    for key in ('starttime','endtime'):
        if not isinstance(state[key],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',state[key]):fail('invalid charge time')
    if not isinstance(state['cycles'],str) or not re.fullmatch(r'[01](?:,[01]){6}',state['cycles']):fail('invalid charge weekday mask')
    if state['chargeEnable'] and '1' not in state['cycles']:fail('enabled charge schedule has no days')
    return state


def seat_position(position,vehicle):
    if position in ('driver','copilot'):
        rudder=getattr(vehicle,'rudder','left')
        if rudder not in ('left','right'):fail('unknown driving side')
        left=(position=='driver')==(rudder=='left')
        return 'left_front' if left else 'right_front'
    if position not in ('left_front','right_front'):fail('only front seats are implemented')
    return position


def bundle(state,vehicle):
    fields(state,set(),{'air_condition','seat_setting','steeringWheelHeatCtrl','rearMirrorHeating','syn_path'})
    if not state:fail('empty preparation bundle')
    out={}
    for key,part in state.items():
        if not isinstance(part,dict) or part.get('enable') is not True:fail('preparation items must be explicitly enabled')
        value={k:v for k,v in part.items() if k!='enable'}
        if key=='air_condition':
            require(vehicle,'170');value=air(value)
        elif key=='syn_path':
            require(vehicle,'180');value=navigation(value)
        elif key=='steeringWheelHeatCtrl':
            require(vehicle,'320');fields(value,{'level'})
            if value['level'] not in ('1','2'):fail('invalid steering heat')
        elif key=='rearMirrorHeating':
            require(vehicle,'440');fields(value,{'value'})
            if value['value'] not in ('1','2'):fail('invalid mirror heat')
        elif key=='seat_setting':
            fields(value,{'driver','copilot','left_rear','right_rear'})
            if value['left_rear']!='0' or value['right_rear']!='0':fail('rear-seat preparation not supported on this B10 adapter')
            for pos in ('driver','copilot'):
                code=value[pos]
                if code not in ('0','1','2','3','11','12','13'):fail('invalid preparation seat level')
                if code!='0':
                    physical=seat_position(pos,vehicle)
                    if code.startswith('1') and len(code)==2:
                        rudder=getattr(vehicle,'rudder','left')
                        driver=(physical=='left_front')==(rudder=='left')
                        require(vehicle,'370',42 if driver else 43)
                    else:require(vehicle,'301')
        out[key]=dict(value,enable=True)
    return out


def appointment(cmd,state,vehicle,*,timezone_name=None,now=None):
    fields(state,{'controls'})
    controls=state['controls']
    if not isinstance(controls,list) or len(controls)>20:fail('invalid appointment count')
    result=[];ids=set()
    for entry in controls:
        common={'days','set_id','start_time','update_time'}
        required=common|({'on'}|AIR_KEYS if cmd=='171' else {'enable','datacontent'})
        fields(entry,required,{'name','desc'})
        text(entry['set_id'],128)
        if not entry['set_id'] or entry['set_id'] in ids:fail('missing or duplicate appointment ID')
        ids.add(entry['set_id'])
        days=entry['days']
        if not isinstance(days,list) or any(type(d)is not int or not 0<=d<=6 for d in days) or len(days)!=len(set(days)):
            fail('invalid appointment weekdays')
        start=local_start(entry['start_time'],timezone_name)
        current=datetime.now(timezone.utc) if now is None else now
        require_aware(current)
        if not days and start<=current:fail('one-shot appointment is in the past')
        finite(entry['update_time'],946684800000,4102444800000)
        out=dict(entry)
        if cmd=='171':
            if entry['on'] not in ('0','1'):fail('invalid appointment enable flag')
            out.update(air({k:entry[k] for k in AIR_KEYS}))
        else:
            if type(entry['enable'])is not bool:fail('invalid preparation enable flag')
            out['datacontent']=bundle(entry['datacontent'],vehicle)
        result.append(out)
    return {'controls':result}


def prepare(cmd,state,vehicle,*,timezone_name=None,now=None):
    model=getattr(vehicle,'car_type',None) or getattr(vehicle,'raw',{}).get('carType','')
    if str(model).upper()!='B10':fail('command contracts currently validated only for B10')
    if cmd not in COMMAND_RULES:fail(UNAVAILABLE.get(cmd,'command contract not implemented for B10: '+cmd))
    if not isinstance(state,dict):fail('payload must be an object')
    ability=None
    if cmd=='370':
        if getattr(vehicle,'rudder','left') not in ('left','right'):fail('unknown driving side')
        physical=seat_position(state.get('position'),vehicle)
        driver=(physical=='left_front')==(getattr(vehicle,'rudder','left')=='left')
        ability=42 if driver else 43
    if cmd=='193' and state.get('value')=='stop':ability=88
    require(vehicle,cmd,ability)
    simple={'110':('value',('lock','unlock')),'120':('value',('true',)),
            '130':('value',('true','false')),'160':('value',('ptcon','ptcoff')),
            '192':('operation',('unlock',)),'193':('value',('start','stop')),
            '240':('value',('0','10')),'320':('level',('1','2')),'440':('value',('1','2'))}
    if cmd in simple:
        field,values=simple[cmd];fields(state,{field})
        if state[field] not in values:fail('unsupported value for '+cmd)
        return state
    if cmd=='230':
        fields(state,{'value'})
        if state['value'] not in tuple(str(i) for i in range(11)):fail('window position must be 0..10')
        return state
    if cmd in ('301','370'):
        fields(state,{'position','level'})
        if state['level'] not in ('0','1','2','3'):fail('seat level must be 0..3')
        return dict(position=seat_position(state['position'],vehicle),level=state['level'])
    if cmd=='170':return air(state)
    if cmd=='180':return navigation(state)
    if cmd=='190':return charge(state)
    if cmd=='360':return bundle(state,vehicle)
    if cmd in ('171','361'):return appointment(cmd,state,vehicle,timezone_name=timezone_name,now=now)
    fail('missing validator')
