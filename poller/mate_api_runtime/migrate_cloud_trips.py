"""Transactional promotion of staged cloud trips into Mate.

Does not log in, send commands, or synthesize GPS/SoC/odometer samples.
Changed cloud records and overlapping local trips require reconciliation.
"""
import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone


def number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid cloud ' + name)
    return value


def timestamp(value):
    if type(value) is not int or not 946684800000 <= value <= 4102444800000:
        raise ValueError('Expected epoch milliseconds')
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def migrate(con):
    if con.in_transaction:
        raise ValueError('Migration requires its own transaction')
    report = dict(inserted=0, unchanged=0, changed=0, overlapping=0)
    con.execute('BEGIN IMMEDIATE')
    try:
        vehicles = dict(con.execute('SELECT vin,id FROM vehicles'))
        staged = con.execute("SELECT payload_json FROM api_lab_cloud_history_records WHERE kind='mileage' ORDER BY record_sha256").fetchall()
        con.execute('''CREATE TABLE IF NOT EXISTS api_lab_cloud_trip_links (
            record_key TEXT PRIMARY KEY, trip_id INTEGER NOT NULL UNIQUE,
            payload_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL,
            max_speed_kmh REAL, source TEXT NOT NULL,
            FOREIGN KEY(trip_id) REFERENCES trips(id))''')
        for (payload,) in staged:
            row = json.loads(payload)
            vin = row.get('vin')
            if vin not in vehicles:
                raise ValueError('Unknown vehicle in staged history')
            start, end = timestamp(row.get('routeStartTs')), timestamp(row.get('routeEndTs'))
            if row['routeEndTs'] < row['routeStartTs']:
                raise ValueError('Reversed cloud trip interval')
            distance = number(row.get('totalMileage'), 'distance')
            energy = number(row.get('totalEnergy'), 'energy')
            speed = row.get('maxSpeed')
            if speed is not None:
                speed = number(speed, 'maximum speed')
            canonical = json.dumps(row, sort_keys=True, separators=(',', ':'))
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            key = hashlib.sha256(json.dumps([vin, row['routeStartTs'], row['routeEndTs']]).encode()).hexdigest()
            linked = con.execute('SELECT trip_id,payload_sha256 FROM api_lab_cloud_trip_links WHERE record_key=?', (key,)).fetchone()
            if linked:
                if not con.execute('SELECT 1 FROM trips WHERE id=?', (linked[0],)).fetchone():
                    report.setdefault('deleted', 0)
                    report['deleted'] += 1
                    continue  # Keep the link as a tombstone: respect the user deletion.
                report['unchanged' if linked[1] == digest else 'changed'] += 1
                continue
            existing = con.execute('''SELECT 1 FROM trips WHERE vehicle_id=?
                AND id NOT IN (SELECT trip_id FROM api_lab_cloud_trip_links) AND
                ((julianday(started_at) < julianday(?) AND
                  (ended_at IS NULL OR julianday(ended_at) > julianday(?))) OR
                 (julianday(started_at)=julianday(?) AND julianday(ended_at)=julianday(?))) LIMIT 1''',
                (vehicles[vin], end, start, start, end)).fetchone()
            if existing:
                report['overlapping'] += 1
                continue
            duration = (row['routeEndTs'] - row['routeStartTs']) / 60000
            efficiency = energy * 100 / distance if distance > 0 else None
            cur = con.execute('''INSERT INTO trips
                (vehicle_id,started_at,ended_at,distance_km,duration_min,
                 ec_kwh,efficiency_kwh_100km,ec_tried,ec_stable,regen_kwh,note,reconstructed)
                VALUES (?,?,?,?,?,?,?,1,1,NULL,?,0)''',
                (vehicles[vin], start, end, distance, duration, energy, efficiency,
                 'Leapmotor cloud history | API lab | GPS/SoC/odometer not supplied'))
            con.execute('INSERT INTO api_lab_cloud_trip_links VALUES (?,?,?,?,?,?)',
                        (key, cur.lastrowid, digest, datetime.now(timezone.utc).isoformat(),
                         speed, 'leapmotor_cloud_history'))
            report['inserted'] += 1
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    args = parser.parse_args()
    with sqlite3.connect(args.database, timeout=30) as db:
        print(json.dumps(migrate(db)))
