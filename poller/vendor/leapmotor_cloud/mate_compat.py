"""Independent, narrow Mate interface. All I/O belongs to the injected adapter.

This is not an emulation of the entire legacy SDK. Only migrated reads and B10
commands are exposed. Unsupported operations fail closed, without fallback.
"""
import copy
import json
import uuid
from dataclasses import dataclass,field
from datetime import datetime,timedelta,time,timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from .errors import ValidationError
from .cloud import _unique_object,_invalid_constant
from .vehicle_data import normalize_telemetry


class MateAPIError(RuntimeError):pass


def adapter_owned_headers(**kwargs):
    """Compatibility marker only: the new adapter signs its own wire request.

    Never use this result with another HTTP sender. It contains no credentials
    and intentionally does not generate obsolete signatures.
    """
    return SimpleNamespace(to_dict=lambda:{})


def _codes(value):
    if value is None or value=='':return ()
    values=value.split(',') if isinstance(value,str) else value
    if not isinstance(values,(tuple,list)):raise ValidationError('Invalid capability list')
    result=[]
    for code in values:
        if isinstance(code,str):
            code=code.strip()
            if not code.isascii() or not code.isdecimal():raise ValidationError('Invalid capability code')
            code=int(code)
        if type(code)is not int or code<=0:raise ValidationError('Invalid capability code')
        result.append(code)
    return tuple(result)


@dataclass(repr=False)
class Vehicle:
    vin: str
    car_type: str
    is_shared: bool
    raw: dict=field(default_factory=dict,repr=False)
    email: str|None=None
    plate_number: str|None=None
    car_id: str|None=None
    user_nickname: str|None=None
    vehicle_nickname: str|None=None
    mobile_number: str|None=None
    out_color: str|None=None
    share_time: object=None
    expire_time: object=None
    duration_type: object=None
    seat_layout: str|None=None
    rudder: str|None=None
    year: object=None
    allocation_code: object=None
    rights: tuple=()
    abilities: tuple=()
    module_rights: tuple=()

    def __repr__(self):return 'Vehicle(<private>)'

    @classmethod
    def from_dict(cls,data,is_shared):
        if (not isinstance(data,dict) or not isinstance(data.get('vin'),str) or not data['vin']
            or not isinstance(data.get('carType'),str) or type(is_shared)is not bool):
            raise ValidationError('Invalid authenticated vehicle binding')
        fields={'email':'email','plate_number':'plateNumber','car_id':'carId',
            'user_nickname':'nickName','vehicle_nickname':'vinNickname','mobile_number':'mobileNumber',
            'out_color':'outColor','share_time':'shareTime','expire_time':'expireTime',
            'duration_type':'durationType','seat_layout':'seatLayout','rudder':'rudder',
            'year':'year','allocation_code':'allocationCode'}
        values={key:data.get(source) for key,source in fields.items()}
        for key in ('car_id','seat_layout','rudder'):
            if values[key] is not None:values[key]=str(values[key])
        return cls(data['vin'],data['carType'],is_shared,copy.deepcopy(data),**values,
            rights=_codes(data.get('rightList')),abilities=_codes(data.get('abilities')),
            module_rights=_codes(data.get('moduleRights')))

    def has_right(self,code):return type(code)is not bool and code in self.rights
    def has_ability(self,code):return type(code)is not bool and code in self.abilities
    def has_module_right(self,code):return type(code)is not bool and code in self.module_rights


class _NoNetwork:
    def close(self):pass
    def __getattr__(self,name):raise MateAPIError('Legacy network access is unavailable')


_ACTIONS={
 'lock':('110',{'value':'lock'}),'unlock':('110',{'value':'unlock'}),
 'unlock_charger':('192',{'operation':'unlock'}),'find_car':('120',{'value':'true'}),
 'trunk_open':('130',{'value':'true'}),'trunk_close':('130',{'value':'false'}),
 'sunshade_open':('240',{'value':'10'}),'sunshade_close':('240',{'value':'0'}),
 'battery_preheat_on':('160',{'value':'ptcon'}),'battery_preheat_off':('160',{'value':'ptcoff'}),
 'charge_start':('193',{'value':'start'}),'charge_stop':('193',{'value':'stop'}),
 'windows':('230',None),'ac_on':('170',None),'ac_switch':('170',None),
 'ac_off':('170',{'operate':'off'}),'ac_schedule':('171',None),
 'set_charge_limit':('190',None),'set_charge_schedule':('190',None),
 'send_destination':('180',None),'seat_heat':('301',None),'seat_ventilation':('370',None),
 'steering_wheel_heat':('320',None),'steering_wheel_heat_on':('320',{'level':'2'}),
 'steering_wheel_heat_off':('320',{'level':'1'}),'rearview_mirror_heat':('440',None),
 'rearview_mirror_heat_on':('440',{'value':'2'}),'rearview_mirror_heat_off':('440',{'value':'1'}),
 'prepare_car':('360',None),
}
_ALIASES={'trunk':'trunk_open','sunshade':'sunshade_open','battery_preheat':'battery_preheat_on'}
_METHODS={'lock_vehicle':'lock','unlock_vehicle':'unlock','unlock_charger':'unlock_charger',
 'open_trunk':'trunk_open','close_trunk':'trunk_close','find_vehicle':'find_car',
 'open_sunshade':'sunshade_open','close_sunshade':'sunshade_close',
 'battery_preheat':'battery_preheat_on','battery_preheat_off':'battery_preheat_off',
 'start_charging':'charge_start','stop_charging':'charge_stop',
 'steering_wheel_heat_on':'steering_wheel_heat_on','steering_wheel_heat_off':'steering_wheel_heat_off',
 'rearview_mirror_heat_on':'rearview_mirror_heat_on','rearview_mirror_heat_off':'rearview_mirror_heat_off'}


class MateClientCompatibility:
    """Adapter superclass with no legacy imports and no HTTP implementation."""
    def __init__(self,*,username,password,app_cert_path,app_key_path,operation_password=None,
                 account_p12_password=None,base_url='',timeout=30,device_id=None,
                 verify_ssl=True,language='en-US',timezone_name=None):
        self.username,self.password=username,password
        self.app_cert_path,self.app_key_path=str(app_cert_path),str(app_key_path)
        self.operation_password=operation_password.strip() if operation_password else None
        self.account_p12_password=account_p12_password
        self.base_url,self.timeout,self.language=base_url.rstrip('/'),timeout,language
        self.device_id=device_id or uuid.uuid4().hex
        self.verify_ssl=True
        self.timezone_name=timezone_name
        self.session=_NoNetwork()
        self.user_id=self.token=self.account_cert_file=self.account_key_file=None
        self.remote_cert_synced=False
        self.last_api_results={}

    def __getattr__(self,name):
        if name in _METHODS:
            return lambda vin:self._remote_control(vin=vin,action=_METHODS[name])
        raise AttributeError(name)

    @property
    def account_cert(self):
        if not self.account_cert_file or not self.account_key_file:raise MateAPIError('Account certificate unavailable')
        return self.account_cert_file,self.account_key_file

    def _auth_headers(self):
        if not self.token or not self.user_id:raise MateAPIError('Authentication required')
        return {'token':self.token,'userId':self.user_id}

    def get_vehicle_list(self):
        self._ensure_token()
        return self._get_vehicle_list()

    def get_vehicle_raw_status(self,vehicle):
        self._ensure_token()
        return self._get_vehicle_raw_status(vehicle)

    def get_vehicle_status(self,vehicle):
        data=self.get_vehicle_raw_status(vehicle)
        return normalize_telemetry(data,expected_vin=vehicle.vin,received_at=datetime.now(timezone.utc))

    def _find_vehicle_by_vin(self,vin):
        for vehicle in self.get_vehicle_list():
            if vehicle.vin==vin:return vehicle
        raise MateAPIError('Vehicle not found in authenticated account')

    def _remote_control(self,*,vin,action,cmd_content=None):
        action=_ALIASES.get(action,action)
        if action not in _ACTIONS:raise MateAPIError('Command not migrated; no legacy fallback')
        cmd,state=_ACTIONS[action]
        if cmd_content is None:
            if state is None:raise MateAPIError('Explicit complete command payload required')
            cmd_content=json.dumps(state,separators=(',',':'))
        return self._remote_control_raw(vin=vin,cmd_id=cmd,cmd_content=cmd_content,action_label=action)

    def _send_state(self,vin,action,state):
        return self._remote_control(vin=vin,action=action,cmd_content=json.dumps(state,separators=(',',':')))

    def windows(self,vin,*,value):return self._send_state(vin,'windows',{'value':str(value)})
    def open_windows(self,vin):return self.windows(vin,value='10')
    def close_windows(self,vin):return self.windows(vin,value='0')
    def control_sunshade(self,vin,*,value):return self._send_state(vin,'sunshade',{'value':str(value)})
    def ac_switch(self,vin,*,params):return self._send_state(vin,'ac_switch',params)
    def ac_off(self,vin):return self._remote_control(vin=vin,action='ac_off')
    def ac_on(self,vin):return self.ac_switch(vin,params=self._climate('nohotcold','26',operate='auto'))

    @staticmethod
    def _climate(mode,temperature,*,operate='manual',wshld='0'):
        return dict(circle='in',mode=mode,operate=operate,position='all',temperature=temperature,windlevel='7',wshld=wshld)

    def quick_cool(self,vin):return self.ac_switch(vin,params=self._climate('cold','18'))
    def quick_heat(self,vin):return self.ac_switch(vin,params=self._climate('hot','32'))
    def windshield_defrost(self,vin):return self.ac_switch(vin,params=self._climate('nohotcold','26',operate='auto',wshld='2'))
    def prepare_car(self,vin,*,params):return self._send_state(vin,'prepare_car',params)
    def sentry_mode_on(self,vin):raise MateAPIError('Sentinel is not validated; command not sent')
    def sentry_mode_off(self,vin):raise MateAPIError('Sentinel is not validated; command not sent')

    def set_climate_schedule(self,vin,*,controls):return self._send_state(vin,'ac_schedule',{'controls':controls})
    def cancel_climate_schedule(self,vin):return self.set_climate_schedule(vin,controls=[])
    def get_charge_schedule(self,vin):
        self._ensure_token()
        return self._get_charge_appointment(vin)

    def set_charge_schedule(self,vin,*,enabled,soc_limit,start_time,end_time,cycles,circulation=0,recharge=0):
        if type(enabled)is not bool:raise MateAPIError('Explicit charge enable flag required')
        return self._send_state(vin,'set_charge_schedule',dict(chargeEnable=int(enabled),chargesoc=soc_limit,
            starttime=start_time,endtime=end_time,cycles=cycles,circulation=circulation,recharge=recharge))

    def send_destination(self,vin,*,address,address_name,latitude,longitude):
        return self._send_state(vin,'send_destination',dict(address=address,addressname=address_name,
            latitude=str(latitude),longitude=str(longitude),linenum='0'))

    def _appointments(self,vin,cmd):
        path='/carownerservice/oversea/vehicle/v1/app/remote/ctl/getAppointment'
        data=self.read(path,{'vin':vin,'cmdId':cmd},form=True)['data']
        if data is None or data=='':return []
        if isinstance(data,str):data=json.loads(data,object_pairs_hook=_unique_object,parse_constant=_invalid_constant)
        if not isinstance(data,dict) or not isinstance(data.get('controls'),list):raise MateAPIError('Invalid schedule response')
        return data['controls']

    def get_climate_schedule(self,vin):return self._appointments(vin,'171')
    def get_prepare_car_schedule(self,vin):return self._appointments(vin,'361')

    def get_message_list(self,*,page_no=1,page_size=10):
        if type(page_no)is not int or page_no<1 or type(page_size)is not int or not 1<=page_size<=100:
            raise MateAPIError('Invalid message pagination')
        data=self.read('/carownerservice/oversea/message/v1/list',{'pageNo':str(page_no),'pageSize':str(page_size)},form=True)['data']
        if not isinstance(data,dict) or not isinstance(data.get('list',[]),list):raise MateAPIError('Invalid message response')
        messages=[SimpleNamespace(title=m.get('title'),message=m.get('message'),send_time=m.get('sendTime')) for m in data.get('list',[])]
        return SimpleNamespace(count=data.get('count'),messages=messages)

    def get_consumption_last_week_breakdown(self,vehicle):
        if not self.timezone_name:raise MateAPIError('Explicit consumption calendar timezone required')
        zone=ZoneInfo(self.timezone_name);today=datetime.now(zone).date()
        monday=today-timedelta(days=today.weekday())
        begin=datetime.combine(monday-timedelta(days=7),time.min,zone)
        end=datetime.combine(monday,time.min,zone)-timedelta(seconds=1)
        data=self.read('/carownerservice/oversea/drivingRecord/v1/getLastweekEC',
            dict(carvin=vehicle.vin,begintime=str(int(begin.timestamp())),endtime=str(int(end.timestamp()))),form=True)['data']
        values={name:float(data[key]) for name,key in [('driver_ec','driverEC'),('ac_ec','acEC'),('other_ec','otherEC')]}
        return SimpleNamespace(**values,total_ec=round(sum(values.values()),2))

    def get_consumption_weekly_rank(self,vehicle):
        data=self.read('/carownerservice/oversea/drivingRecord/v1/getLastNweeks100kmECAndRank',{'carvin':vehicle.vin},form=True)['data']
        rank=data.get('rankResult') or {}
        def measurement(row,key):return None if row.get(key) is None else float(row[key])
        weekly=[SimpleNamespace(week_start=w.get('weekStart'),week_end=w.get('weekEnd'),
            hundred_km_ec=measurement(w,'hundredKmEC'),hundred_mi_kwh_ec=measurement(w,'hundredMiKwhEC'),
            week_start_ms=w.get('xWeekStart'),week_end_ms=w.get('xWeekEnd')) for w in data.get('weeklyEC',[])]
        return SimpleNamespace(rank=SimpleNamespace(result=rank.get('result'),rank=rank.get('rank'),
            hundred_km_ec=measurement(rank,'hundredKmEC'),hundred_mi_kwh_ec=measurement(rank,'hundredMiKwhEC')),weekly=weekly)

    def get_car_picture(self,vehicle):raise MateAPIError('Picture endpoint not migrated')
    def download_car_picture_package(self,**kwargs):raise MateAPIError('Binary endpoint not migrated')
