"""Deleting an imported item must neither restore it nor stop later imports."""
import json
import sqlite3
import pytest
import mate_api


@pytest.mark.parametrize('kind,module,target,link,column,payload', [
    ('mileage','migrate_cloud_trips','trips','api_lab_cloud_trip_links','trip_id',
     {'vin':'TEST','routeStartTs':1700000000000,'routeEndTs':1700000060000,'totalMileage':1,'totalEnergy':0}),
    ('charge','migrate_cloud_charges','charges','api_lab_cloud_charge_links','charge_id',
     {'vin':'TEST','chargeEarliestTs':1700000000000,'chargeLatestTs':1700000060000,'chargeInEnergy':1}),
])
def test_deleted_cloud_record_remains_deleted(kind,module,target,link,column,payload):
    import hashlib
    import importlib
    db=sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE vehicles(vin TEXT,id INTEGER)')
        db.execute("INSERT INTO vehicles VALUES('TEST',1)")
        db.execute(f'CREATE TABLE {target}(id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE api_lab_cloud_history_records(kind TEXT,payload_json TEXT,record_sha256 TEXT,imported_at TEXT)')
        db.execute('INSERT INTO api_lab_cloud_history_records VALUES(?,?,?,?)',(kind,json.dumps(payload),'hash','now'))
        db.execute(f'CREATE TABLE {link}(record_key TEXT,{column} INTEGER,payload_sha256 TEXT)')
        endpoints=([payload['routeStartTs'],payload['routeEndTs']] if kind=='mileage' else [payload['chargeEarliestTs']])
        key=hashlib.sha256(json.dumps(['TEST',*endpoints]).encode()).hexdigest()
        db.execute(f'INSERT INTO {link} VALUES(?,?,?)',(key,99,'oldhash'))
        db.commit()
        report=importlib.import_module(module).migrate(db)
        assert report['deleted']==1
        assert db.execute(f'SELECT COUNT(*) FROM {target}').fetchone()[0]==0
        assert db.execute(f'SELECT COUNT(*) FROM {link}').fetchone()[0]==1
    finally:
        db.close()


def test_history_starts_after_factory_reset_and_authentication():
    import ast
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'poller'/'main.py').read_text()
    tree=ast.parse(source)
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    calls={}
    for n in ast.walk(main):
        if isinstance(n,ast.Call):
            name=n.func.id if isinstance(n.func,ast.Name) else getattr(n.func,'attr','')
            calls.setdefault(name,[]).append(n.lineno)
    assert min(calls['start_history_worker']) > max(calls['factory_reset']+calls['ensure_vehicle']+calls['login'])
