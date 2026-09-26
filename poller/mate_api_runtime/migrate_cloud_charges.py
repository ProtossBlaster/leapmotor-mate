"""Transactional, idempotent cloud charge import for the isolated 4001 lab."""
import hashlib
import json
import math
from datetime import datetime, timezone
from migrate_cloud_trips import number, timestamp


def coordinate(value, limit):
    if value is None or value=='':return None
    if type(value)is bool:raise ValueError('Invalid coordinate')
    value=float(value)
    if not math.isfinite(value) or abs(value)>limit:raise ValueError('Invalid coordinate')
    return value


def migrate(con):
    if con.in_transaction:raise ValueError('Import requires its own transaction')
    report=dict(inserted=0,unchanged=0,changed=0,overlapping=0)
    con.execute('BEGIN IMMEDIATE')
    try:
        vehicles=dict(con.execute('SELECT vin,id FROM vehicles'))
        con.execute('''CREATE TABLE IF NOT EXISTS api_lab_cloud_charge_links (
            record_key TEXT PRIMARY KEY, charge_id INTEGER NOT NULL UNIQUE,
            payload_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL,
            source TEXT NOT NULL, FOREIGN KEY(charge_id) REFERENCES charges(id))''')
        rows=con.execute("SELECT payload_json FROM api_lab_cloud_history_records WHERE kind='charge' ORDER BY imported_at,record_sha256").fetchall()
        for (payload,) in rows:
            row=json.loads(payload);vin=row.get('vin')
            if vin not in vehicles:raise ValueError('Unknown charge vehicle')
            start,end=timestamp(row.get('chargeEarliestTs')),timestamp(row.get('chargeLatestTs'))
            if row['chargeLatestTs']<row['chargeEarliestTs']:raise ValueError('Reversed charge interval')
            energy=number(row.get('chargeInEnergy'),'charge energy')
            lat=coordinate(row.get('chargeStartLatitude'),90)
            lon=coordinate(row.get('chargeStartLongitude'),180)
            if lat is None or lon is None or (lat==0 and lon==0):lat=lon=None
            digest=hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            key=hashlib.sha256(json.dumps([vin,row.get('chargeGunStartTs') or row['chargeEarliestTs']]).encode()).hexdigest()
            linked=con.execute('SELECT charge_id,payload_sha256 FROM api_lab_cloud_charge_links WHERE record_key=?',(key,)).fetchone()
            if linked:
                if not con.execute('SELECT 1 FROM charges WHERE id=?',(linked[0],)).fetchone():raise ValueError('Imported charge removed')
                report['unchanged' if linked[1]==digest else 'changed']+=1
                continue
            overlap=con.execute('''SELECT 1 FROM charges WHERE vehicle_id=? AND
                ((julianday(started_at)<julianday(?) AND
                (ended_at IS NULL OR julianday(ended_at)>julianday(?))) OR
                (julianday(started_at)=julianday(?) AND julianday(ended_at)=julianday(?))) LIMIT 1''',
                (vehicles[vin],end,start,start,end)).fetchone()
            if overlap:
                report['overlapping']+=1;continue
            kind={'SLOW':'AC','FAST':'DC'}.get(row.get('chargeType'))
            cur=con.execute('''INSERT INTO charges
                (vehicle_id,started_at,ended_at,energy_added_kwh,duration_min,latitude,longitude,
                 charge_type,note,reconstructed,close_reason)
                VALUES (?,?,?,?,?,?,?,?,?,0,?)''',
                (vehicles[vin],start,end,energy,(row['chargeLatestTs']-row['chargeEarliestTs'])/60000,
                 lat,lon,kind,'Leapmotor cloud chargeInEnergy; not a wallbox meter reading. SoC/cost not supplied.',
                 'cloud_history'))
            con.execute('INSERT INTO api_lab_cloud_charge_links VALUES (?,?,?,?,?)',
                (key,cur.lastrowid,digest,datetime.now(timezone.utc).isoformat(),'leapmotor_cloud_history'))
            report['inserted']+=1
        con.commit()
    except BaseException:
        con.rollback();raise
    return report
