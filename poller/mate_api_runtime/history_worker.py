"""Read-only cloud synchronization + local lab import; no vehicle controls."""
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import crypto
from pathlib import Path
from api_v2_bridge import DB, NewAPIClient, LeapmotorApiError, connect_db, setting, set_setting
from leapmotor_cloud.transport import TransportError
from cloud_import_policy import import_trips
from migrate_cloud_trips import migrate
from migrate_cloud_charges import migrate as migrate_charges


class _Reader:
    """One history page, read with whatever client this installation already runs.

    The independent client signs its own wire request and knows each vehicle's route. The
    previous client does not expose `read`, so the page is signed here with its own key and
    handed to its transport. Both reach the same unified path — measured 27/09/2026 against
    the real cloud: `/carownerservice/mileage/daily/detail/page` answers result=0 under the
    previous client's signature too. Nothing new is logged in: the session is the one the
    installation is already using.
    """

    def __init__(self, api):
        self._api = api
        self._signs_its_own = hasattr(api, 'read')

    def route(self, vin):
        return self._api.route(vin) if self._signs_its_own else {'appCenter': None}

    def read(self, path, body, origin=None):
        if self._signs_its_own:
            return self._api.read(path, body, origin=origin)
        from leapmotor_api.crypto import build_signed_headers
        headers = build_signed_headers(sign_key=self._api.sign_key, device_id=self._api.device_id,
                                       language=self._api.language,
                                       body_params={k: str(v) for k, v in body.items()}).to_dict()
        headers.update(self._api._auth_headers())
        headers['Content-Type'] = 'application/json'
        response = self._api._post_json(path=path, headers=headers, json_body=body,
                                        cert=self._api.account_cert)
        raw = response.get('body')
        try:
            envelope = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            raise LeapmotorApiError('Cloud history could not be decoded') from None
        if not isinstance(envelope, dict):
            raise LeapmotorApiError('Cloud history returned an invalid envelope')
        codes = [envelope[key] for key in ('code', 'result') if key in envelope]
        if not codes or any(type(code) is bool or str(code) != '0' for code in codes):
            # A refusal — no permission for this model, a vehicle that lost its rights — is
            # reported as unavailable, so the caller skips that vehicle and not the whole sync.
            raise LeapmotorApiError('Cloud history unavailable for this vehicle')
        if not isinstance(envelope.get('data'), dict):
            raise LeapmotorApiError('Cloud history returned no page')
        return envelope


def _reader(api):
    return _Reader(api)


def _client(username, password, device, on_login=None):
    """The client this installation selected for commands, holding its live session.

    On a retained account the previous client is shared through `session_share`, so the
    worker reuses the poller's token instead of knocking on the login endpoint the cloud
    started rationing on 17/09/2026. A login it does spend is told to `on_login`, like the
    poller's own.
    """
    certs = Path(DB).parent / 'certs'
    if os.environ.get('MATE_API_V2') == '0':
        from leapmotor_api import LeapmotorApiClient
        import session_share
        api = LeapmotorApiClient(username=username, password=password,
                                 app_cert_path=str(certs / 'app.crt'),
                                 app_key_path=str(certs / 'app.key'),
                                 language='en-US', device_id=device)
        session_share.install(api)
        api.on_login = on_login
        return api
    api = NewAPIClient(username=username, password=password, device_id=device,
                       app_cert_path=str(certs / 'app.crt'),
                       app_key_path=str(certs / 'app.key'), language='en-US')
    api.on_login = on_login
    return api


def sync_once(on_login=None):
    with connect_db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return
        db.execute('''CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records (
            kind TEXT NOT NULL, record_sha256 TEXT NOT NULL, source TEXT NOT NULL,
            imported_at TEXT NOT NULL, payload_json TEXT NOT NULL,
            PRIMARY KEY(kind,record_sha256))''')
        username=crypto.decrypt(setting(db,'leapmotor_user')) or os.environ.get('LEAPMOTOR_USER', '')
        password=crypto.decrypt(setting(db,'leapmotor_pass')) or os.environ.get('LEAPMOTOR_PASS', '')
        device=setting(db,'mate_device_id')
        if not device:
            device='mate-'+secrets.token_hex(16)
            set_setting(db,'mate_device_id',device)
        local_vins={row[0] for row in db.execute('SELECT vin FROM vehicles')}
    if not username or not password or not local_vins:return
    api=_client(username,password,device,on_login)
    reader=_reader(api)
    # Persisted cars survive account changes; only current authenticated bindings
    # may authorize a cloud read. New cars wait until the poller registers them.
    vins=list(dict.fromkeys(vehicle.vin for vehicle in api.get_vehicle_list()
                           if vehicle.vin in local_vins and type(vehicle.is_shared) is bool))
    if not vins:return
    zone=ZoneInfo(os.environ.get('TZ', 'UTC'))
    now=datetime.now(timezone.utc);local=now.astimezone(zone)
    start=datetime(local.year,local.month,1,tzinfo=zone)
    summary={}
    for vin in vins:
        try:
            route=reader.route(vin)
            for kind in ('mileage','charge'):
                records=[];seen=set();baseline=None;complete=False
                for page in range(1,101):
                    size=50 if kind=='charge' else 20
                    body=dict(vin=vin,pageNum=str(page) if kind=='charge' else page,
                              pageSize=str(size) if kind=='charge' else size,
                              startTime=str(int(start.timestamp())),endTime=str(int(now.timestamp())))
                    data=reader.read('/carownerservice/'+kind+'/daily/detail/page',body,origin=route['appCenter'])['data']
                    if any(type(data.get(k))is not int for k in ('pageNum','pageSize','totalPage','total')):
                        raise ValueError('Invalid history pagination')
                    if data['pageNum']!=page or data['pageSize']!=size:raise ValueError('Unexpected history page')
                    totals=(data['total'],data['totalPage'])
                    if baseline is not None and totals!=baseline:raise ValueError('History changed during paging')
                    baseline=totals;rows=data.get('list')
                    if not isinstance(rows,list):raise ValueError('Invalid history records')
                    for row in rows:
                        if not isinstance(row,dict):raise ValueError('Invalid history record')
                        if kind=='charge':
                            if row.get('vin',vin)!=vin:raise ValueError('History vehicle mismatch')
                            # Charge DTO has no VIN; bind to the authenticated request.
                            row=dict(row,vin=vin)
                        elif row.get('vin')!=vin:raise ValueError('History vehicle mismatch')
                        key=json.dumps(row,sort_keys=True,separators=(',',':'))
                        if key in seen:raise ValueError('Duplicate history record')
                        seen.add(key);records.append(row)
                    if len(records)>=data['total']:
                        complete=len(records)==data['total'];break
                    if not rows:break
                if not complete:raise ValueError('Incomplete history batch; not imported')
                with connect_db() as db:
                    for row in records:
                        payload=json.dumps(row,sort_keys=True,separators=(',',':'))
                        db.execute('INSERT OR IGNORE INTO api_lab_cloud_history_records VALUES (?,?,?,?,?)',
                            (kind,hashlib.sha256(payload.encode()).hexdigest(),'api-v2-worker',now.isoformat(),payload))
                summary[kind]=summary.get(kind, 0)+len(records)
        except (LeapmotorApiError, TransportError):
            # Rights may disappear between listing and reading, or one route may
            # be unavailable. Continue other current vehicles without logging VINs,
            # credentials, payloads, or upstream exception messages.
            summary['unavailable_vehicles']=summary.get('unavailable_vehicles', 0)+1
            continue
    with connect_db() as db:
        summary['trip_import']=import_trips(db, migrate)
        summary['charge_import']=migrate_charges(db)
        summary['at']=now.isoformat()
        set_setting(db,'api_v2_history_sync',json.dumps(summary))
    print(json.dumps({'api_v2_history_sync':summary}),flush=True)


if __name__=='__main__':
    while True:
        try:sync_once()
        except Exception as exc:
            print(json.dumps({'api_v2_history_error':type(exc).__name__}),flush=True)
        time.sleep(300)
