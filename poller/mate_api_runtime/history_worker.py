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
from cloud_import_policy import import_from, import_trips, past_months, trips_enabled
from migrate_cloud_trips import migrate
from migrate_cloud_charges import migrate as migrate_charges


def _client(username, password, device, on_login=None):
    """The installation's client, holding its live session: it signs its own reads and knows each
    vehicle's route. A login it does spend is told to `on_login`, like the poller's own."""
    certs = Path(DB).parent / 'certs'
    api = NewAPIClient(username=username, password=password, device_id=device,
                       app_cert_path=str(certs / 'app.crt'),
                       app_key_path=str(certs / 'app.key'), language='en-US')
    api.on_login = on_login
    return api


def _read_window(api, route, vin, kind, start, end):
    """Every record the cloud holds for one window, or ValueError: a batch is staged whole or not at all."""
    records=[];seen=set();baseline=None;complete=False
    for page in range(1,101):
        size=50 if kind=='charge' else 20
        body=dict(vin=vin,pageNum=str(page) if kind=='charge' else page,
                  pageSize=str(size) if kind=='charge' else size,
                  startTime=str(int(start.timestamp())),endTime=str(int(end.timestamp())))
        data=api.read('/carownerservice/'+kind+'/daily/detail/page',body,origin=route['appCenter'])['data']
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
    return records


def _stage(kind, records, now, month=None):
    """Stage one window's records; an earlier month, `(vin, 'YYYY-MM')`, is marked asked with them."""
    with connect_db() as db:
        for row in records:
            payload=json.dumps(row,sort_keys=True,separators=(',',':'))
            db.execute('INSERT OR IGNORE INTO api_lab_cloud_history_records VALUES (?,?,?,?,?)',
                (kind,hashlib.sha256(payload.encode()).hexdigest(),'api-v2-worker',now.isoformat(),payload))
        if month:
            db.execute('INSERT OR REPLACE INTO api_lab_cloud_history_months VALUES (?,?,?,?)',
                (month[0],kind,month[1],now.isoformat()))


def sync_once(on_login=None):
    with connect_db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return
        db.execute('''CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records (
            kind TEXT NOT NULL, record_sha256 TEXT NOT NULL, source TEXT NOT NULL,
            imported_at TEXT NOT NULL, payload_json TEXT NOT NULL,
            PRIMARY KEY(kind,record_sha256))''')
        # The earlier months already asked whole, per car and kind: each is asked once.
        db.execute('''CREATE TABLE IF NOT EXISTS api_lab_cloud_history_months (
            vin TEXT NOT NULL, kind TEXT NOT NULL, month TEXT NOT NULL, asked_at TEXT NOT NULL,
            PRIMARY KEY(vin,kind,month))''')
        username=crypto.decrypt(setting(db,'leapmotor_user')) or os.environ.get('LEAPMOTOR_USER', '')
        password=crypto.decrypt(setting(db,'leapmotor_pass')) or os.environ.get('LEAPMOTOR_PASS', '')
        device=setting(db,'mate_device_id')
        if not device:
            device='mate-'+secrets.token_hex(16)
            set_setting(db,'mate_device_id',device)
        local_vins={row[0] for row in db.execute('SELECT vin FROM vehicles')}
        first=import_from(db) if trips_enabled(db) else None
        done={tuple(row) for row in db.execute('SELECT vin,kind,month FROM api_lab_cloud_history_months')}
    if not username or not password or not local_vins:return
    api=_client(username,password,device,on_login)
    # Persisted cars survive account changes; only current authenticated bindings
    # may authorize a cloud read. New cars wait until the poller registers them.
    vins=list(dict.fromkeys(vehicle.vin for vehicle in api.get_vehicle_list()
                           if vehicle.vin in local_vins and type(vehicle.is_shared) is bool))
    if not vins:return
    zone=ZoneInfo(os.environ.get('TZ', 'UTC'))
    now=datetime.now(timezone.utc);local=now.astimezone(zone)
    start=datetime(local.year,local.month,1,tzinfo=zone)
    # The earlier months the user chose to import, whole months in the user's time zone. The cloud
    # is asked from the first of the CURRENT month above, so without these an installation made on
    # the 1st never sees the month before it (Silvio's MateDesktop, 01/10/2026).
    months=[(datetime(y,m,1,tzinfo=zone),datetime(y+m//12,m%12+1,1,tzinfo=zone),f'{y:04d}-{m:02d}')
            for y,m in past_months(local.date()) if first and (y,m)>=first]
    summary={}
    for vin in vins:
        try:
            route=api.route(vin)
            for kind in ('mileage','charge'):
                records=_read_window(api,route,vin,kind,start,now)
                _stage(kind,records,now)
                summary[kind]=summary.get(kind, 0)+len(records)
            for month_start,month_end,month in months:
                for kind in ('mileage','charge'):
                    if (vin,kind,month) in done:continue
                    try:records=_read_window(api,route,vin,kind,month_start,month_end)
                    except ValueError:
                        # Neither staged nor marked: asked again at the next synchronization, while
                        # the current month above is staged and imported as usual.
                        summary['months_incomplete']=summary.get('months_incomplete', 0)+1
                        continue
                    _stage(kind,records,now,(vin,month))
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
