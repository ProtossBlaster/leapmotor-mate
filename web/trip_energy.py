"""Read-only EV energy selection; never replace stored trip telemetry."""

import json
import math
from datetime import datetime


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _cloud_time(value):
    value = _number(value)
    if value is None or not 946684800000 <= value <= 4102444800000:
        return None
    return value / 1000


def _within_distance(a, b):
    return abs(a - b) <= max(0.5, 0.1 * a)


def select_energy(db, displayed):
    """Annotate EV rows only, with conservative full-trip cloud matching.

    Cloud records may be rounded. We allow 90 seconds at the boundaries,
    require at least 80% time coverage and compatible distance, and reject
    overlapping records, conflicting versions and ambiguous local matches.
    These are matching safeguards, not claimed Leapmotor trip thresholds.
    """
    if not displayed:
        return displayed
    settings = dict(db.execute("SELECT key, value FROM settings"))
    vehicles = {r["id"]: r["vin"] for r in db.execute("SELECT id, vin FROM vehicles")}
    ev_ids = {
        key for key, vin in vehicles.items()
        if settings.get("is_reev_" + str(vin).lower(), settings.get("is_reev")) == "0"
    }
    if not ev_ids:
        return displayed
    raw = {
        r["id"]: dict(r) for r in db.execute("SELECT * FROM trips WHERE ended_at IS NOT NULL")
        if r["vehicle_id"] in ev_ids
    }
    bounds = {key: (_time(r["started_at"]), _time(r["ended_at"])) for key, r in raw.items()}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    links = set()
    if "api_lab_cloud_trip_links" in tables:
        links = {r[0] for r in db.execute("SELECT trip_id FROM api_lab_cloud_trip_links")}
    records = {}
    conflicts = set()
    if "api_lab_cloud_history_records" in tables:
        for row in db.execute("SELECT payload_json FROM api_lab_cloud_history_records WHERE kind='mileage'"):
            try:
                record = json.loads(row[0])
                vin = record["vin"]
                start = _cloud_time(record.get("routeStartTs"))
                end = _cloud_time(record.get("routeEndTs"))
                energy = _number(record.get("totalEnergy"))
                distance = _number(record.get("totalMileage"))
                if start is None or end is None or end <= start:
                    continue
                key = (vin, start, end)
                value = (energy, distance)
                if key in records and records[key] != value:
                    conflicts.add(key)
                records[key] = value
            except (TypeError, ValueError, KeyError):
                continue
    assigned = {}
    blocked = set()
    for key, (energy, distance) in records.items():
        vin, start, end = key
        candidates = []
        for trip_id, trip in raw.items():
            a, b = bounds[trip_id]
            if vehicles.get(trip["vehicle_id"]) != vin or a is None or b is None or b <= a:
                continue
            if min(end, b) > max(start, a) and start >= a - 90 and end <= b + 90:
                candidates.append(trip_id)
        if len(candidates) != 1:
            blocked.update(candidates)
            continue
        trip_id = candidates[0]
        if key in conflicts or energy is None or distance is None:
            blocked.add(trip_id)
            continue
        assigned.setdefault(trip_id, []).append((start, end, energy, distance))
    cloud = {}
    for trip_id, records_for_trip in assigned.items():
        trip = raw[trip_id]
        if trip_id in blocked or trip.get("reconstructed"):
            continue
        records_for_trip.sort()
        a, b = bounds[trip_id]
        km = _number(trip.get("distance_km"))
        if km is None or abs(records_for_trip[0][0] - a) > 90 or abs(records_for_trip[-1][1] - b) > 90:
            continue
        if any(left[1] > right[0] for left, right in zip(records_for_trip, records_for_trip[1:])):
            continue
        covered = sum(max(0, min(end, b) - max(start, a)) for start, end, _, _ in records_for_trip)
        if covered < 0.8 * (b - a):
            continue
        if not _within_distance(km, sum(item[3] for item in records_for_trip)):
            continue
        cloud[trip_id] = sum(item[2] for item in records_for_trip)
    children = {}
    for trip_id, trip in raw.items():
        if trip.get("merged_into_id") is not None:
            children.setdefault(trip["merged_into_id"], []).append(trip_id)
    for trip in displayed:
        trip_id = trip.get("id")
        if trip_id not in raw:
            continue
        segments = [trip_id]
        for segment in segments:
            for child in children.get(segment, []):
                if child not in segments:
                    segments.append(child)
        km = _number(trip.get("distance_km"))
        eff = _number(trip.get("efficiency_kwh_100km"))
        estimate = eff * km / 100 if eff is not None and km is not None else None
        measured = _number(trip.get("ec_kwh")) if trip.get("ec_stable") else None
        trip["mate_fallback_energy_kwh"] = estimate
        trip["getec_fallback_energy_kwh"] = measured
        cloud_value = None
        if all(segment in cloud for segment in segments):
            intervals = sorted(bounds[segment] for segment in segments)
            if all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:])):
                segment_km = sum(raw[segment].get("distance_km") or 0 for segment in segments)
                if km is not None and _within_distance(km, segment_km):
                    cloud_value = sum(cloud[segment] for segment in segments)
        trip["cloud_energy_kwh"] = cloud_value
        if cloud_value is not None:
            energy, source = cloud_value, "cloud"
        elif measured is not None and trip_id not in links:
            energy, source = measured, "getec"
        elif trip_id in links:
            # Imported cloud values must not be relabelled as getEC or Mate.
            energy, source = None, None
        else:
            energy, source = estimate, "mate" if estimate is not None else None
        if source is None:
            continue
        trip["energy_source"] = source
        trip["energy_kwh"] = energy
        trip["efficiency_kwh_100km"] = energy * 100 / km if km is not None and km > 0 else None
        if source == "cloud":
            trip["ec_pending"] = False
    return displayed
