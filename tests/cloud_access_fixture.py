"""Explicit synthetic authenticated owner binding for UI/discovery layout tests."""
import mate_api
import json
import time
import crypto
from ui_command_access import account_hash, snapshot_key

ABILITIES = [3,6,9,10,11,12,13,14,15,19,21,35,38,42,43,48,52]

def settings(vin, abilities=ABILITIES, car_type='B10'):
    user='synthetic-layout-account'
    raw={'vin':vin,'carType':car_type,'abilities':abilities}
    values={'leapmotor_user':crypto.encrypt(user), snapshot_key(vin):json.dumps({
        'account':account_hash(user),'at':time.time(),'vehicle':raw,'shared':False})}
    return lambda key, default='': values.get(key,default)
