"""Account-scoped UI hints. The backend independently rechecks cloud rights."""
import hashlib
import json
import os
import time
from types import SimpleNamespace
from command_contracts import require, prepare
from leapmotor_cloud.mate_compat import Vehicle

GROUPS = {
 '110': 'lock unlock', '120': 'find_car', '130': 'open_trunk close_trunk',
 '160': 'battery_preheat battery_preheat_off',
 '170': 'ac_on ac_off climate_off quick_cool quick_heat quick_vent windshield_defrost climate_defrost recirc_toggle set_climate_temp set_fan_level set_recirc',
 '171': 'climate_schedule', '180': 'navigation', '190': 'set_charge_limit save_charge_schedule',
 '192': 'unlock_charger', '230': 'open_windows close_windows set_windows',
 '240': 'open_sunshade close_sunshade', '301': 'seat_heat_driver_on seat_heat_driver_off seat_heat_passenger_on seat_heat_passenger_off',
 '370': 'seat_vent_driver_on seat_vent_driver_off seat_vent_passenger_on seat_vent_passenger_off',
 '320': 'steering_heat_on steering_heat_off', '440': 'mirror_heat_on mirror_heat_off',
 '360': 'prepare_car', '361': 'prepare_schedule',
}
COMMANDS = {name: cmd for cmd, names in GROUPS.items() for name in names.split()}


def snapshot_key(vin):
    return 'api_v2_access_' + (vin or '').lower()


def account_hash(username):
    return hashlib.sha256(username.encode()).hexdigest()


def allowed(snapshot, username, vin, command, *, now=None):
    if command in ('sentry_on', 'sentry_off'):
        return False
    cmd = COMMANDS.get(command)
    if cmd is None:
        return False
    try:
        now = time.time() if now is None else now
        if snapshot['account'] != account_hash(username) or not 0 <= now - snapshot['at'] <= 300:
            return False
        raw = snapshot['vehicle']
        if raw.get('vin') != vin or raw.get('carType', '').upper() != 'B10':
            return False
        shared = snapshot['shared']
        if type(shared) is not bool:
            return False
        v = Vehicle.from_dict(raw,is_shared=shared)
        if cmd in ('301','370'):
            side = 'copilot' if 'passenger' in command else 'driver'
            prepare(cmd, {'position':side, 'level':'1'}, v)
        else:
            require(v, cmd)
        return True
    except Exception:
        return False


def account_username(get_setting):
    import crypto
    return crypto.decrypt(get_setting('leapmotor_user', '')) or os.environ.get('LEAPMOTOR_USER', '')


def command_allowed(vin, command, get_setting):
    try:
        username = account_username(get_setting)
        snapshot = json.loads(get_setting(snapshot_key(vin), '{}'))
        return allowed(snapshot, username, vin, command)
    except Exception:
        return False


def hidden_controls_css(vin, get_setting):
    selectors = []
    for name in list(COMMANDS) + ['sentry_on','sentry_off']:
        if not command_allowed(vin, name, get_setting):
            selectors.append('[hx-post="api/command/' + name + '"]')
    routes = {'set_windows':'api/windows', 'set_climate_temp':'api/climate-temp',
              'set_fan_level':'api/climate-fan'}
    for func in ('heat','vent'):
        for side, label in (('driver','driver'),('copilot','passenger')):
            routes['seat_' + func + '_' + label + '_on'] = 'api/seat/' + func + '/' + side
    for name, route in routes.items():
        if not command_allowed(vin, name, get_setting):
            selectors.append('[hx-post="' + route + '"]')
    return ','.join(selectors) + '{display:none!important}' if selectors else ''
