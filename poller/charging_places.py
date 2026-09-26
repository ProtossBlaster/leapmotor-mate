"""Private charging places. Pure SQLite/domain helpers shared by web and poller.

Places are per vehicle; each charge keeps a name/rate snapshot. No global tariff,
wallbox connection, recorded GPS or historical charge is changed by editing a place.
"""
import math
import time


def validate(name, latitude, longitude, radius_m, rate):
    name = str(name or '').strip()
    if not name or len(name) > 80:
        raise ValueError('place_invalid')
    try:
        lat, lon, radius, price = map(float, (latitude, longitude, radius_m, rate))
    except (TypeError, ValueError):
        raise ValueError('place_invalid') from None
    if (not all(math.isfinite(x) for x in (lat, lon, radius, price))
            or not -90 <= lat <= 90 or not -180 <= lon <= 180
            or (lat == 0 and lon == 0) or not 25 <= radius <= 500 or not 0 <= price <= 100):
        raise ValueError('place_invalid')
    return name, lat, lon, radius, price


def distance_m(lat, lon, other_lat, other_lon):
    a, b = math.radians(lat), math.radians(other_lat)
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(other_lon-lon)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def match(db, vehicle_id, data, now=None):
    # Unknown or stale position, moving car, unknown/DC connector: leave it to the owner.
    try:
        lat, lon = float(data.latitude), float(data.longitude)
        age = (time.time() if now is None else now) - float(data.timestamp_ms)/1000
        if (not all(math.isfinite(x) for x in (lat, lon, age))
                or not -90 <= lat <= 90 or not -180 <= lon <= 180
                or (lat == 0 and lon == 0) or not 0 <= age <= 120
                or data.gear != 'P' or data.speed_kmh != 0
                or getattr(data, 'dc_gun_connected', None) is not False):
            return None
    except (AttributeError, TypeError, ValueError):
        return None
    hits = [dict(p) for p in db.execute(
        'SELECT * FROM charging_places WHERE vehicle_id=? AND enabled=1', (vehicle_id,))
        if distance_m(lat, lon, p['latitude'], p['longitude']) <= p['radius_m']]
    return hits[0] if len(hits) == 1 else None


def snapshot(db, charge_id, place, source):
    db.execute('UPDATE charges SET charging_place_id=?, charging_place_name=?, '
               'charging_place_rate=?, charging_place_source=? WHERE id=?',
               (place['id'], place['name'], place['rate'], source, charge_id))


def cost(charge, billed=None):
    """Frozen place tariff; caller owns manual overrides, FREE and energy validation."""
    energy = billed if billed is not None and billed > 0 else (charge.get('energy_added_kwh') or 0)
    if charge.get('is_free') or charge.get('location_type') == 'FREE':
        return 0.0
    if energy <= 0:
        return None
    return round(energy * charge['charging_place_rate'], 2)
