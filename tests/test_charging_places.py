"""Private places must never guess a location or rewrite another session's tariff."""
import time
from types import SimpleNamespace

import pytest
import db as D
import db_reader as W
import charging_places as P
from schema import ensure_schema


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path/'places.db'))
    db.ensure_vehicle('CAR1', 'B10', 2025)
    db.ensure_vehicle('CAR2', 'B10', 2025)
    monkeypatch.setattr(W, 'DB_PATH', str(tmp_path/'places.db'))
    monkeypatch.setattr(W, '_current_vehicle_id', lambda: 1)
    return db


def place(store, vehicle=1, name='Home', lat=45, rate=.3):
    cur=store._conn.execute('INSERT INTO charging_places (vehicle_id,name,latitude,longitude,radius_m,rate) VALUES (?,?,?,9,100,?)', (vehicle,name,lat,rate))
    store._conn.commit()
    return cur.lastrowid


def frame(**overrides):
    d=dict(latitude=45,longitude=9,timestamp_ms=int(time.time()*1000),gear='P',speed_kmh=0,
           dc_gun_connected=False,soc=50,odometer_km=1000)
    d.update(overrides)
    return SimpleNamespace(**d)


def closed(store, pid=None, **values):
    cid=store.create_charge(1,frame())
    store._conn.execute("UPDATE charges SET ended_at='2026-09-26T12:00:00+00:00',energy_added_kwh=10 WHERE id=?",(cid,))
    for k,v in values.items():
        store._conn.execute(f'UPDATE charges SET {k}=? WHERE id=?',(v,cid))
    store._conn.commit()
    return cid


def row(store,cid):
    return dict(store._conn.execute('SELECT * FROM charges WHERE id=?',(cid,)).fetchone())


def test_auto_snapshot_is_per_vehicle_and_editing_place_does_not_change_it(store):
    pid=place(store);place(store,vehicle=2,rate=.9)
    cid=store.create_charge(1,frame())
    assert row(store,cid)['charging_place_rate']==.3
    store._conn.execute('UPDATE charging_places SET rate=.8,name="Changed" WHERE id=?',(pid,));store._conn.commit()
    assert row(store,cid)['charging_place_name']=='Home'
    assert row(store,cid)['charging_place_rate']==.3
    assert row(store,cid)['location_type']=='HOME'


@pytest.mark.parametrize('change',[{'latitude':None},{'longitude':None},{'latitude':0,'longitude':0},
 {'latitude':float('nan')},{'longitude':181},{'timestamp_ms':0},
 {'timestamp_ms':int((time.time()-600)*1000)},{'timestamp_ms':int((time.time()+600)*1000)},
 {'gear':'D'},{'speed_kmh':1},{'dc_gun_connected':None},{'dc_gun_connected':True},{'latitude':46}])
def test_unreliable_or_outside_position_never_matches(store,change):
    place(store)
    assert P.match(store._conn,1,frame(**change)) is None


def test_overlapping_and_disabled_zones(store):
    a=place(store);b=place(store,name='Nearby',lat=45.0001)
    assert P.match(store._conn,1,frame()) is None
    store._conn.execute('UPDATE charging_places SET enabled=0 WHERE id=?',(b,))
    assert P.match(store._conn,1,frame())['id']==a


def test_reconstructed_charge_does_not_use_current_position(store):
    place(store)
    cid=store.create_reconstructed_charge(1,40,'2026-09-25T10:00:00+00:00',frame())
    assert row(store,cid)['charging_place_id'] is None


def test_manual_assignment_preserves_cost_and_gps(store):
    pid=place(store,lat=46)
    cid=closed(store,cost=12.34,cost_manual=1)
    W.assign_charging_place(cid,pid)
    r=row(store,cid)
    assert (r['cost'],r['cost_manual'],r['latitude'])==(12.34,1,45)
    assert r['charging_place_source']=='manual'
    W.set_charge_cost(cid,None)
    assert row(store,cid)['cost']==3


def test_frozen_rate_survives_retag_and_global_price_changes(store):
    place(store);cid=closed(store)
    W.set_setting('price_home_kwh','9')
    W.update_charge_type(cid,'HOME')
    assert row(store,cid)['cost']==3
    W.update_charge_type(cid,'AC')
    assert row(store,cid)['cost']==3


def test_zero_rate_free_flag_and_measured_energy(store):
    pid=place(store,rate=0);cid=closed(store)
    W.update_charge_type(cid,'HOME');assert row(store,cid)['cost']==0
    store._conn.execute('UPDATE charges SET charging_place_rate=.3,ac_energy_kwh=12 WHERE id=?',(cid,));store._conn.commit()
    W.update_charge_type(cid,'HOME');assert row(store,cid)['cost']==3.6
    W.update_charge_type(cid,'HOME',_free=1);assert row(store,cid)['cost']==0


def test_manual_assignment_rejects_other_car_and_open_or_merged_charge(store):
    pid=place(store,vehicle=2)
    cid=closed(store)
    with pytest.raises(ValueError):W.assign_charging_place(cid,pid)
    pid=place(store)
    active=store.create_charge(1,frame())
    with pytest.raises(ValueError):W.assign_charging_place(active,pid)
    store._conn.execute('UPDATE charges SET merged_into_id=? WHERE id=?',(active,cid));store._conn.commit()
    with pytest.raises(ValueError):W.assign_charging_place(cid,pid)


def test_schema_is_repeatable_and_does_not_classify_history(store):
    cid=closed(store);before=row(store,cid)
    place(store)
    ensure_schema(store._conn);ensure_schema(store._conn)
    assert row(store,cid)==before


@pytest.mark.parametrize('args',[('',45,9,100,.3),('X',float('nan'),9,100,.3),
 ('X',45,9,0,.3),('X',45,9,100,-1),('X',45,9,100,float('inf'))])
def test_validation(args):
    with pytest.raises(ValueError):P.validate(*args)


def test_costs_page_and_picker_render_and_save_is_vehicle_scoped(store):
    from starlette.testclient import TestClient
    import main
    W.set_setting('setup_complete','1')
    client=TestClient(main.app)
    payload=dict(vehicle_id=1,name='Weekend',latitude=46,longitude=9,radius_m=100,rate=.25,enabled='on')
    r=client.post('/api/settings/charging-places',data=payload,follow_redirects=False)
    assert r.status_code==303
    assert 'Weekend' in client.get('/costs').text
    cid=closed(store)
    assert 'Weekend' in client.get(f'/api/charges/{cid}/place').text
    pid=place(store,vehicle=2)
    assert client.post('/api/settings/charging-places',data=dict(payload,id=pid)).status_code==400


def test_finalize_uses_snapshot_after_meter_validation_and_preserves_manual(store):
    place(store)
    cid=store.create_charge(1,frame())
    store._conn.execute("UPDATE charges SET started_at='2026-09-25T10:00:00+00:00' WHERE id=?",(cid,));store._conn.commit()
    store.finalize_charge(cid,frame(soc=60),max_power_kw=3)
    r=row(store,cid)
    assert r['cost']==round(r['energy_added_kwh']*.3,2)
    cid=store.create_charge(1,frame())
    store._conn.execute("UPDATE charges SET started_at='2026-09-25T10:00:00+00:00',cost=5,cost_manual=1 WHERE id=?",(cid,));store._conn.commit()
    store.finalize_charge(cid,frame(soc=60),max_power_kw=3)
    assert row(store,cid)['cost']==5


def test_removing_place_uses_existing_tariff_without_touching_manual_total(store):
    place(store);cid=closed(store,cost=8,cost_manual=1)
    W.assign_charging_place(cid,0)
    assert row(store,cid)['charging_place_rate'] is None
    assert row(store,cid)['cost']==8


def test_totals_are_per_vehicle_and_include_manual_cost(store):
    place(store);cid=closed(store,cost=8,cost_manual=1)
    store._conn.execute("INSERT INTO charges(vehicle_id,ended_at,cost,charging_place_id,charging_place_name) VALUES (2,'2026-09-26',999,1,'Home')");store._conn.commit()
    totals=W.charging_places_context()['charging_place_totals']
    assert totals==[dict(name='Home',sessions=1,cost=8,unpriced=0)]


def test_a_stale_form_cannot_save_a_place_on_another_car(store):
    with pytest.raises(ValueError):
        W.save_charging_place(dict(vehicle_id=2,name='Home',latitude=45,longitude=9,radius_m=100,rate=.2))
    assert W.charging_places_context()['charging_places']==[]


def test_different_place_snapshots_cannot_be_merged(store):
    a=place(store);cid=closed(store)
    store._conn.execute('UPDATE charging_places SET rate=.6 WHERE id=?',(a,));store._conn.commit()
    other=closed(store)
    assert W.merge_charges(cid,other)['error']=='different_charging_place'


def test_free_charge_remains_free_when_a_place_is_assigned(store):
    pid=place(store,lat=46);cid=closed(store,location_type='FREE',cost=0)
    W.assign_charging_place(cid,pid)
    assert row(store,cid)['cost']==0
    assert row(store,cid)['is_free']==1


@pytest.mark.parametrize('group_field',['cost_manual','gross_kwh'])
def test_group_total_does_not_mark_children_unpriced(store,group_field):
    place(store)
    parent=closed(store,cost=8,**{group_field:1})
    closed(store,cost=None,merged_into_id=parent)
    totals=W.charging_places_context()['charging_place_totals']
    assert totals[0]['cost']==8
    assert totals[0]['unpriced']==0
