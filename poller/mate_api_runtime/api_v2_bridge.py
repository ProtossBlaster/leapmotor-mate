"""Mate independent cloud adapter. No legacy authentication or command fallback.

Local DTOs, commands and certificate-password derivation use MATE-API.
The old module namespace is patched only at the Mate integration boundary.
"""
import base64
from process_lock import exclusive
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from leapmotor_cloud.private_storage import validate_private_file
from urllib.parse import parse_qsl, urlencode

import crypto
from leapmotor_cloud.mate_compat import MateClientCompatibility, Vehicle, MateAPIError as LeapmotorApiError
from leapmotor_cloud.pin import encrypt_operate_password
from leapmotor_cloud.authentication import LoginClient, LoginUnavailable
from leapmotor_cloud.operating_availability import OperatingState, operating_decision
from leapmotor_cloud.models import Availability
from leapmotor_cloud.transport import UrllibTransport, Request, validate_url
from leapmotor_cloud.cloud import ALLOWED_HOSTS, GLOBAL_ORIGIN, CENTER_ORIGIN, _unique_object, _invalid_constant
from leapmotor_cloud.signing import sign_authenticated
from leapmotor_cloud.session import session_device_id
from leapmotor_cloud.b10_commands import interpret
from command_contracts import COMMAND_RULES, APPOINTMENT_PATH, prepare, charge
from session_material import certificate_usable

DB = os.environ.get('DB_PATH', '/data/leapmotor_mate.db')
SESSION_KEY = 'api_v2_shared_session'
LOGIN_PATH = '/base/base-user/account/v1/login'
READ_PATHS = {
    '/carownerservice/oversea/vehicle/v1/carpicture/key',
    '/carownerservice/oversea/vehicle/v1/carpicture/package',
    '/app/app-global-service/v1/vehicle/list',
    '/app/app-global-service/v1/vehicle/getCarRoute',
    '/app/app-signal-service/signal/info/query',
    '/carownerservice/v3/api/vehicleinfo/commonConfig',
    '/carownerservice/charge/daily/detail/page',
    '/carownerservice/charge/statistic/days',
    '/carownerservice/charge/vail/startTime/query',
    '/carownerservice/mileage/daily/detail/page',
    '/carownerservice/mileage/vail/startTime/query',
    '/carownerservice/oversea/drivingRecord/v1/getLastweekEC',
    '/carownerservice/oversea/drivingRecord/v1/getPlugInLastNweeks100kmEC',
    '/carownerservice/oversea/drivingRecord/v1/getLastNweeks100kmECAndRank',
    '/carownerservice/oversea/drivingRecord/v1/mileage/energy/detail',
    '/carownerservice/oversea/message/v1/list',
    '/carownerservice/oversea/message/v1/unread/count',
    '/carownerservice/oversea/vehicle/v1/app/remote/ctl/getAppointment',
}
CONTROL_PATH = '/app/app-control-service/v3/api/appremotectl'
VERIFY_PATH = '/carownerservice/oversea/vehicle/v1/operPwd/verify'
COMMAND_ABILITIES = {cmd:rule[1] for cmd,rule in COMMAND_RULES.items()}


def connect_db():
    return sqlite3.connect(DB, timeout=30)


def setting(db, key, default=''):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_setting(db, key, value):
    db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', (key,value))


class NoLegacyNetwork:
    def close(self):
        pass

    def __getattr__(self, name):
        raise LeapmotorApiError('Direct legacy network access disabled in independent API runtime')


class NewAPIClient(MateClientCompatibility):
    _mate_new_api = True

    def __init__(self, **kwargs):
        kwargs['verify_ssl'] = True
        kwargs['timezone_name'] = os.environ.get('TZ')
        super().__init__(**kwargs)
        with connect_db() as db:
            installation_id = setting(db, 'mate_device_id')
            if not installation_id:
                installation_id = secrets.token_hex(16)
                set_setting(db, 'mate_device_id', installation_id)
        self._installation_device_id = installation_id
        self.session.close()
        self.session = NoLegacyNetwork()
        self._transport = UrllibTransport(Path(__file__).with_name('leapmotor-appsubca-public.pem'), ALLOWED_HOSTS)
        self._mutex = threading.RLock()
        self._new_key = None
        self._routes = {}
        self._access_refresh_attempt = None
        self.last_new_command_receipt = None

    def _audit(self, path, method, status, code):
        with connect_db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS api_v2_http_log
                (id INTEGER PRIMARY KEY, at TEXT, method TEXT, path TEXT, http_status INTEGER, api_code TEXT)''')
            db.execute('INSERT INTO api_v2_http_log(at,method,path,http_status,api_code) VALUES (?,?,?,?,?)',
                       (datetime.now(timezone.utc).isoformat(),method,path,status,str(code)))
            db.execute('DELETE FROM api_v2_http_log WHERE id < (SELECT MAX(id)-10000 FROM api_v2_http_log)')

    def _wire(self, origin, path, body, *, method='POST', form=False, login=False, binary=False):
        origin = validate_url(origin, ALLOWED_HOSTS, origin=True)
        if path not in READ_PATHS | {LOGIN_PATH,CONTROL_PATH,VERIFY_PATH,CONTROL_PATH+'/query',APPOINTMENT_PATH}:
            raise LeapmotorApiError('Endpoint not migrated; legacy fallback disabled')
        core = dict(source='leapmotor',channel='1',acceptLanguage=self.language,
                    version='V1.16.4-1',deviceType='android',nonce=str(secrets.randbelow(10**15)),
                    timestamp=str(int(time.time()*1000)),deviceId=self.device_id)
        if login:
            combined = dict(core, **body)
            sign = hashlib.sha256(''.join(str(combined[k]) for k in sorted(combined)).encode()).hexdigest().upper()
            cert = (Path(self.app_cert_path), Path(self.app_key_path))
        else:
            sign = sign_authenticated(self._new_key, core, {k:str(v) for k,v in body.items()})
            cert = (Path(self.account_cert_file), Path(self.account_key_file))
        headers = dict(core,sign=sign,carvin=body.get('vin',body.get('carvin','')),cartype='' if login else 'B10')
        headers.update({'Content-Type':'application/x-www-form-urlencoded' if form else 'application/json',
                        'x-region':'EU','x-api-signature-version':'2.0','X-P12_ENC_ALG':'1'})
        if not login:
            headers.update(token=self.token,userId=str(self.user_id))
        url = origin + path
        if method == 'GET':
            url += '?' + urlencode(body)
            data = None
        else:
            data = urlencode(body).encode() if form else json.dumps(body,separators=(',',':')).encode()
        transport = self._transport
        if binary:
            transport = UrllibTransport(Path(__file__).with_name('leapmotor-appsubca-public.pem'),
                                        ALLOWED_HOSTS, max_response_bytes=16 * 1024 * 1024)
        response = transport.send(Request(method,url,headers,data),client_cert=cert)
        if response.status==401 and not login:
            self._invalidate_session(headers['token'])
            self._audit(path,method,response.status,'authentication_rejected')
            raise LeapmotorApiError('Session rejected; command/read not retried')
        if binary and response.status == 200 and response.body[:4] == b'PK\x03\x04':
            import io, zipfile
            if len(response.body) > 32 * 1024 * 1024:
                raise LeapmotorApiError('Picture package too large')
            with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 128 * 1024 * 1024:
                    raise LeapmotorApiError('Expanded picture package too large')
            self._audit(path,method,response.status,'binary_ok')
            return {}, response
        try:
            envelope = json.loads(response.body.decode(),object_pairs_hook=_unique_object,parse_constant=_invalid_constant)
        except Exception:
            self._audit(path,method,response.status,'invalid_json')
            raise LeapmotorApiError('New API response could not be decoded') from None
        if not isinstance(envelope,dict):
            raise LeapmotorApiError('New API returned an invalid envelope')
        codes = [envelope[k] for k in ('code','result') if k in envelope]
        self._audit(path,method,response.status,codes)
        if not login and any(str(code) == '302002004' for code in codes):
            self._invalidate_session(headers['token'])
            raise LeapmotorApiError('Session unavailable; invalidated without replay')
        if response.status != 200 or not codes or any(type(c) is bool or str(c)!='0' for c in codes):
            raise LeapmotorApiError('New API rejected request: HTTP %s, code %s' % (response.status,str(codes)[:100]))
        if binary:
            raise LeapmotorApiError('Picture endpoint did not return a ZIP')
        return envelope, response

    def _apply_session(self, saved):
        if not certificate_usable(saved['cert'], saved['private_key']):
            raise LeapmotorApiError('Account certificate unavailable or expiring')
        if getattr(self, 'user_id', None) != saved['user_id']:
            self._routes = {}
            self._access_refresh_attempt = None
        self.token=saved['token'];self.user_id=saved['user_id']
        self.device_id=session_device_id(self.token,saved['device_id'])
        self._new_key=base64.b64decode(saved['key'],validate=True)
        if len(self._new_key)!=32:raise ValueError('Invalid session key')
        self.account_cert_file=saved['cert'];self.account_key_file=saved['private_key']
        self.remote_cert_synced=True

    def _authenticate_session(self):
        def material(data):
            self._load_account_cert(dict(data, id=data['accountId']))
            return Path(self.account_cert_file), Path(self.account_key_file)
        client=LoginClient(self._transport,
            application_cert=(Path(self.app_cert_path),Path(self.app_key_path)),
            account_certificate_provider=material,
            clock=lambda:datetime.now(timezone.utc),
            nonce_factory=lambda:str(secrets.randbelow(10**15)),language=self.language)
        try:
            session=client.login(self.username,self.password,device_id=self._installation_device_id)
        except LoginUnavailable as error:
            self._audit(LOGIN_PATH,'POST',error.http_status or 0,
                        str(error.api_code) if error.api_code is not None else 'login_'+error.stage)
            detail='stage='+error.stage
            if error.http_status is not None:detail+='; HTTP='+str(error.http_status)
            if error.api_code is not None:detail+='; API='+str(error.api_code)
            with connect_db() as db:
                set_setting(db,'api_v2_login_failure',detail)
            raise LeapmotorApiError('New API sign-in unavailable; '+detail+'; no automatic retry') from None
        self._audit(LOGIN_PATH,'POST',200,'0')
        return session

    def _load_account_cert(self, login_data):
        from leapmotor_cloud.account_material import AccountMaterialProvider
        from leapmotor_cloud.account_password import AccountPasswordResolver
        def candidates(data):
            explicit=getattr(self,'account_p12_password',None)
            parameters=Path(DB).parent/'api-v2-private'/'p12-parameters.json'
            if not parameters.exists():
                if explicit:return [explicit.encode('utf-8')]
                raise LeapmotorApiError('Private application password parameters unavailable')
            validate_private_file(parameters)
            if parameters.stat().st_size>32768:
                raise LeapmotorApiError('Private application password parameters unsafe')
            config=json.loads(parameters.read_text(),object_pairs_hook=_unique_object,parse_constant=_invalid_constant)
            return AccountPasswordResolver(**config).candidates(data,explicit=explicit)
        root=Path(DB).parent/'api-v2-account-material'
        try:
            cert,key=AccountMaterialProvider(root,candidates)(login_data)
        except Exception:
            raise LeapmotorApiError('Account certificate material unavailable') from None
        self.account_cert_file=str(cert);self.account_key_file=str(key)

    def login(self):
        with self._mutex:
            lock_path = Path(DB).parent/'api-v2-session.lock'
            with exclusive(lock_path):
                with connect_db() as db:
                    raw=setting(db,SESSION_KEY)
                    try:
                        saved=json.loads(crypto.decrypt(raw))
                        valid=(saved['username_hash']==hashlib.sha256(self.username.encode()).hexdigest()
                               and saved['expires_at']>time.time()+90
                               and certificate_usable(saved['cert'],saved['private_key']))
                        if valid:
                            self._apply_session(saved)
                            return
                    except (ValueError,KeyError,TypeError):
                        pass
                    try:
                        last_attempt = float(setting(db, 'api_v2_login_attempt', '0'))
                    except (TypeError, ValueError):
                        last_attempt = 0
                    if (os.environ.get('MATE_LAB_LOGIN_ONCE') != '1'
                            and 0 <= time.time() - last_attempt < 60):
                        raise LeapmotorApiError('Login temporarily deferred after a recent attempt')
                    set_setting(db,'api_v2_login_attempt',str(time.time()))
                if not self.username or not self.password:
                    raise LeapmotorApiError('New API account credentials are missing')
                session=self._authenticate_session()
                saved=dict(token=session.token,user_id=session.user_id,device_id=session.device_id,
                           key=base64.b64encode(session.key).decode(),cert=str(session.client_cert[0]),
                           private_key=str(session.client_cert[1]),expires_at=session.expires_at.timestamp(),
                           username_hash=hashlib.sha256(self.username.encode()).hexdigest())
                with connect_db() as db:
                    set_setting(db,SESSION_KEY,crypto.encrypt(json.dumps(saved)))
                    set_setting(db,'device_id',self._installation_device_id)
                    set_setting(db,'api_v2_backend','active')
                    set_setting(db,'api_v2_login_failure','')
                self._apply_session(saved)

    @property
    def sign_key(self):
        self.login()
        return self._new_key

    def _ensure_token(self):
        self.login()

    def token_refresh(self):
        # Coordinated reauthentication, not the obsolete refresh endpoint.
        with connect_db() as db:
            raw=setting(db,SESSION_KEY)
            if raw:
                saved=json.loads(crypto.decrypt(raw))
                if saved.get('token')==self.token:
                    saved['expires_at']=0
                    set_setting(db,SESSION_KEY,crypto.encrypt(json.dumps(saved)))
        self.login()

    def _invalidate_session(self, rejected_token):
        lock_path=Path(DB).parent/'api-v2-session.lock'
        with exclusive(lock_path):
            with connect_db() as db:
                try:saved=json.loads(crypto.decrypt(setting(db,SESSION_KEY)))
                except (ValueError,TypeError):return False
                if (saved.get('token')!=rejected_token or saved.get('username_hash')!=
                    hashlib.sha256(self.username.encode()).hexdigest()):return False
                saved['expires_at']=0
                set_setting(db,SESSION_KEY,crypto.encrypt(json.dumps(saved)))
                set_setting(db,'api_v2_login_attempt',str(time.time()))
            self._routes={}
            return True

    def _clear_auth(self):
        self.token=None

    def _clear_account_cert_files(self):
        # Shared files belong to the session generation, not to one process.
        pass

    def close(self):
        pass

    def _retry_on_token_expiry(self, func, *args, **kwargs):
        return func(*args,**kwargs)

    def read(self,path,body,*,origin=CENTER_ORIGIN,method='POST',form=False):
        if path not in READ_PATHS:raise LeapmotorApiError('Read endpoint not migrated')
        with self._mutex:
            self.login()
            return self._wire(origin,path,body,method=method,form=form)[0]

    def _get_vehicle_list(self):
        self._access_refresh_attempt = time.monotonic()
        envelope=self.read('/app/app-global-service/v1/vehicle/list',{},origin=GLOBAL_ORIGIN,method='GET')
        data=envelope.get('data') or {}
        from ui_command_access import snapshot_key, account_hash
        vehicles=[]
        with connect_db() as db:
            for bucket,shared in [('bindcars',False),('sharedcars',True)]:
                for row in data.get(bucket,[]):
                    if not row.get('vin'):continue
                    vehicles.append(Vehicle.from_dict(row,is_shared=shared))
                    raw={key:row.get(key) for key in ('vin','carType','rightList','moduleRights','abilities','rudder')}
                    set_setting(db,snapshot_key(row['vin']),json.dumps(dict(
                        account=account_hash(self.username),at=time.time(),shared=shared,vehicle=raw)))
        return vehicles

    def route(self,vin):
        cached=self._routes.get(vin)
        if cached and time.time()-cached[0]<300:return cached[1]
        data=self.read('/app/app-global-service/v1/vehicle/getCarRoute',{'vin':vin},origin=GLOBAL_ORIGIN,method='GET')['data']
        if data.get('vin')!=vin:raise LeapmotorApiError('Vehicle route mismatch')
        for name in ('appCenter','appRegion'):
            validate_url(data.get(name),ALLOWED_HOSTS,origin=True)
        self._routes[vin]=(time.time(),data)
        return data

    def _refresh_command_access(self):
        # Healthy polling must renew UI permissions before their 300-second TTL.
        # Throttle failed attempts too; a permissions outage must not stop telemetry.
        with self._mutex:
            last = getattr(self, '_access_refresh_attempt', None)
            if last is not None and 0 <= time.monotonic() - last < 240:
                return
            self._access_refresh_attempt = time.monotonic()
            try:
                self.get_vehicle_list()
            except Exception:
                pass  # Existing snapshots expire normally; never extend stale permissions.

    def _get_vehicle_raw_status(self,vehicle):
        self._refresh_command_access()
        route=self.route(vehicle.vin)
        telemetry=self.read('/app/app-signal-service/signal/info/query',{'vin':vehicle.vin},origin=route['appRegion'])
        data=dict(telemetry.get('data') or {})
        if data.get('vin')!=vehicle.vin or not isinstance(data.get('signalMap'),dict):
            raise LeapmotorApiError('New API telemetry mismatch')
        config=self.read('/carownerservice/v3/api/vehicleinfo/commonConfig',
            dict(vin=vehicle.vin,osType='Android',appVersion='V1.16.4-1'),origin=route['appCenter'],method='GET')
        config_data=config.get('data') or {}
        if config_data.get('vin')!=vehicle.vin:raise LeapmotorApiError('New API configuration mismatch')
        data['signal']=data['signalMap']
        data['config']=config_data.get('config',{})
        return dict(code=0,result=0,data=data)

    def _get_charge_appointment(self,vin):
        route=self.route(vin)
        d=self.read('/carownerservice/v3/api/vehicleinfo/commonConfig',dict(vin=vin,osType='Android',appVersion='V1.16.4-1'),origin=route['appCenter'],method='GET')['data']
        if not isinstance(d, dict) or d.get('vin') != vin:
            raise LeapmotorApiError('New API configuration mismatch')
        c=d.get('config',{}).get('3',{})
        def integer(key):
            value = c.get(key)
            # Cloud configuration permits decimal strings; retain unknown values so
            # strict command validation still rejects missing/invalid full state.
            if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
                return int(value)
            return value
        return dict(chargeEnable=integer('isEnable'),chargesoc=integer('percent'),starttime=c.get('beginTime'),
                    endtime=c.get('endTime'),cycles=c.get('cycles'),circulation=integer('circulation'),recharge=integer('recharge'))

    def _post(self,*,path,headers,data,cert):
        envelope=self.read(path,dict(parse_qsl(data,keep_blank_values=True)),form=True)
        return dict(status_code=200,body=json.dumps(envelope))

    def _post_json(self,*,path,headers,json_body,cert):
        envelope=self.read(path,json_body)
        return dict(status_code=200,body=json.dumps(envelope))

    def _get(self,*,path,headers,params,cert):
        envelope=self.read(path,params,method='GET')
        return dict(status_code=200,body=json.dumps(envelope))

    def _post_binary(self,**kwargs):
        raise LeapmotorApiError('Binary endpoint not migrated; no legacy fallback')

    def get_car_picture(self, vehicle):
        route = self.route(vehicle.vin)
        self._picture_origin = route['appCenter']
        return self.read('/carownerservice/oversea/vehicle/v1/carpicture/key',
                         {'deviceID': self.device_id, 'vin': vehicle.vin},
                         origin=self._picture_origin, form=True)

    def download_car_picture_package(self, *, picture_key):
        if not isinstance(picture_key, str) or not picture_key or len(picture_key) > 4096:
            raise LeapmotorApiError('Invalid picture key')
        with self._mutex:
            self.login()
            _, response = self._wire(getattr(self, '_picture_origin', CENTER_ORIGIN),
                '/carownerservice/oversea/vehicle/v1/carpicture/package',
                {'key': picture_key}, form=True, binary=True)
            return response.body

    def _remote_control_without_pin_raw(self,**kwargs):
        # Navigation now uses the reconstructed sender. This lab conservatively
        # requires the configured PIN, rather than falling back to legacy HTTP.
        if str(kwargs.get('cmd_id'))!='180':
            raise LeapmotorApiError('Command without vehicle PIN not enabled in independent API runtime')
        return self._remote_control_raw(**kwargs)

    def _remote_control(self,*,vin,action,cmd_content=None):
        return super()._remote_control(vin=vin,action=action,cmd_content=cmd_content)

    def set_charge_limit(self,vin,charge_limit_percent):
        current=self._get_charge_appointment(vin)
        charge(current)  # Never replace an unreadable schedule with defaults.
        current['chargesoc']=charge_limit_percent
        return self._remote_control_raw(vin=vin,cmd_id='190',cmd_content=json.dumps(current),action_label='set_charge_limit')

    def _remote_control_raw(self,*,vin,cmd_id,cmd_content,action_label,vehicle=None):
        with self._mutex:
            self.last_new_command_receipt=None
            cmd_id=str(cmd_id)
            vehicle=next((v for v in self.get_vehicle_list() if v.vin==vin),None)
            if vehicle is None or vehicle.car_type.upper()!='B10':
                raise LeapmotorApiError('New command adapter currently limited to B10')
            if not isinstance(cmd_content,str) or len(cmd_content)>32768:
                raise LeapmotorApiError('Invalid command payload size')
            state=prepare(cmd_id,json.loads(cmd_content,object_pairs_hook=_unique_object,
                                           parse_constant=_invalid_constant),vehicle,
                          timezone_name=os.environ.get('TZ'))
            signals=self._get_vehicle_raw_status(vehicle)['data']['signal']
            def num(key):
                try:
                    value=float(signals[key])
                    return value if math.isfinite(value) and type(signals[key]) is not bool else None
                except (KeyError,ValueError,TypeError):return None
            timestamp=num('1')
            try:observed=datetime.fromtimestamp(timestamp/1000,timezone.utc) if timestamp is not None else None
            except (ValueError,OverflowError,OSError):observed=None
            # Remote commands must remain available while the vehicle sleeps.
            # Age alone cannot distinguish sleep from an unavailable live reading.
            # Retain timestamp validity and last-known stationary/ON3 checks;
            # an old parked reading does NOT establish the current vehicle state.
            # Permission/PIN checks and a single cloud submission still apply.
            # Acceptance does not confirm wake-up or physical execution.
            configuration_only=cmd_id in ('171','180','190','361')
            speed,on3=num('1319'),num('1258')
            operating=OperatingState(vin,observed,None if speed is None or speed<0 else speed!=0,
                                     None if on3 is None or on3<0 else on3!=0)
            decision=operating_decision(operating,now=datetime.now(timezone.utc),
                state_max_age=timedelta(seconds=60),allow_stale_parked=True)
            if not configuration_only and decision.state is not Availability.AVAILABLE:
                raise LeapmotorApiError('Command not sent: '+decision.reason)
            if not self.operation_password:raise LeapmotorApiError('Vehicle PIN is missing')
            self.login()
            encrypted=encrypt_operate_password(self.operation_password,self.token)
            route=self.route(vin)
            self._wire(route['appCenter'],VERIFY_PATH,dict(vin=vin,operatePassword=encrypted),form=True)
            appointment=cmd_id in ('171','361')
            try:
                envelope,response=self._wire(route['appCenter'] if appointment else route['appRegion'],
                    APPOINTMENT_PATH if appointment else CONTROL_PATH,
                    dict(carvin=vin,cmdid=cmd_id,state=json.dumps(state,separators=(',',':')),oppwd=encrypted),form=True)
            except Exception:
                raise LeapmotorApiError('Remote control result unknown; command was not retried') from None
            receipt=interpret(response)
            self.last_new_command_receipt=receipt
            return dict(envelope,_new_api_outcome=receipt.outcome)

    def seat_heat(self,vin,*,position,level):
        return self._seat(vin,'301',position,level)

    def seat_ventilation(self,vin,*,position,level):
        return self._seat(vin,'370',position,level)

    def _seat(self,vin,cmd_id,position,level):
        if type(position)is not int or position not in (1,2) or type(level)is not int or level not in (0,1,2,3):
            raise LeapmotorApiError('Only front seats and levels 0..3 are enabled')
        return self._remote_control_raw(vin=vin,cmd_id=cmd_id,
            cmd_content=json.dumps(dict(position='left_front' if position==1 else 'right_front',level=str(level))),action_label='seat')


def install():
    import session_share
    session_share.install=lambda api:api
    session_share.ensure_account_cert=lambda api: bool(api.account_cert_file and Path(api.account_cert_file).is_file())
