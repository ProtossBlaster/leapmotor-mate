"""Read-only EV energy and top-speed selection; never replace stored trip telemetry."""

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



def _first_at_or_after(values, target):
    """Index of the first value >= target, by halving. Written out instead of importing `bisect`
    because every import here has to be declared in MateDesktop's payload contract, and a six-line
    search is not worth coupling a release to a shell rebuild."""
    low, high = 0, len(values)
    while low < high:
        middle = (low + high) // 2
        if values[middle] < target:
            low = middle + 1
        else:
            high = middle
    return low

class _TripIndex:
    """Which trips a cloud record could possibly belong to.

    A record can only belong to a trip that had already started when the record did
    (`start >= a - 90`), so the trips worth testing are the ones whose start is just before it.
    Sorted once, they are found by bisection instead of by walking the whole history for every
    record: that walk is quadratic in the history and was 32 ms of the 92 ms an add-on spent
    showing three trips, growing with every drive recorded.
    → tests/test_matching_a_cloud_record_does_not_scan_every_trip.py

    One class, two callers: `select_energy` reads `totalEnergy` off the matched records and
    `cloud_fuel_by_trip` reads `driveReevOil`. The window, the 90-second tolerance and the check
    are the same question in both cases, and a second copy of them would drift — this file already
    carries a comment about two lists drifting, which is how that lesson was learnt.
    """

    def __init__(self, raw, bounds, vehicles):
        self._raw, self._bounds, self._vehicles = raw, bounds, vehicles
        self._ordered = sorted(
            ((bounds[trip_id][0], trip_id) for trip_id, trip in raw.items()
             if bounds[trip_id][0] is not None and bounds[trip_id][1] is not None
             and bounds[trip_id][1] > bounds[trip_id][0]),
            key=lambda pair: pair[0])
        self._starts = [pair[0] for pair in self._ordered]
        # How far back a qualifying trip can have started: the longest one there is. Taken from the
        # data, so the window can never cut off a trip that the full walk would have found.
        self._longest = max((bounds[trip_id][1] - bounds[trip_id][0]
                             for _, trip_id in self._ordered), default=0)

    def candidates(self, vin, start, end):
        found = []
        upper = _first_at_or_after(self._starts, start + 90 + 1e-9)
        lower = _first_at_or_after(self._starts, start - self._longest - 90)
        for index in range(lower, upper):
            trip_id = self._ordered[index][1]
            a, b = self._bounds[trip_id]
            if self._vehicles.get(self._raw[trip_id]["vehicle_id"]) != vin:
                continue
            if min(end, b) > max(start, a) and start >= a - 90 and end <= b + 90:
                found.append(trip_id)
        return found


def _cars_of_kind(db, reev):
    """The vehicle ids that are (or are not) range-extenders, by the same per-car-then-account rule
    `db_reader.is_reev_car` uses. Absence of the per-car key falls back to the account flag."""
    settings = dict(db.execute("SELECT key, value FROM settings"))
    vehicles = {r["id"]: r["vin"] for r in db.execute("SELECT id, vin FROM vehicles")}
    wanted = "1" if reev else "0"
    ids = {key for key, vin in vehicles.items()
           if settings.get("is_reev_" + str(vin).lower(), settings.get("is_reev")) == wanted}
    return vehicles, ids


def _assign_records(index, records, conflicts, raw, bounds):
    """Records to trips, under the safeguards: one unambiguous candidate, no conflicting versions,
    boundaries within 90 s, at least 80 % of the trip covered, pieces that do not overlap, and a
    distance that agrees. Returns {trip_id: [(start, end, key)]} for the trips that pass."""
    assigned, blocked = {}, set()
    for key in records:
        vin, start, end = key
        found = index.candidates(vin, start, end)
        if len(found) != 1:
            blocked.update(found)
            continue
        trip_id = found[0]
        if key in conflicts:
            blocked.add(trip_id)
            continue
        assigned.setdefault(trip_id, []).append((start, end, key))
    kept = {}
    for trip_id, pieces in assigned.items():
        if trip_id in blocked or raw[trip_id].get("reconstructed"):
            continue
        pieces.sort()
        a, b = bounds[trip_id]
        km = _number(raw[trip_id].get("distance_km"))
        if km is None or abs(pieces[0][0] - a) > 90 or abs(pieces[-1][1] - b) > 90:
            continue
        if any(left[1] > right[0] for left, right in zip(pieces, pieces[1:])):
            continue
        covered = sum(max(0, min(end, b) - max(start, a)) for start, end, _ in pieces)
        if covered < 0.8 * (b - a):
            continue
        if not _within_distance(km, sum(records[key][1] for _, _, key in pieces)):
            continue
        kept[trip_id] = pieces
    return kept


def _segments_of(raw):
    """Each trip with the pieces merged INTO it, so a figure for a joined drive is the sum of its
    parts rather than the parent's alone."""
    children = {}
    for trip_id, trip in raw.items():
        if trip.get("merged_into_id") is not None:
            children.setdefault(trip["merged_into_id"], []).append(trip_id)
    return children


def cloud_fuel_by_trip(db):
    """{trip_id: litres} — what the car's OWN cloud says each range-extender drive burned.

    Both figures come from the car. On the one drive a third party settles — @ebagnoli's 19/09/2026
    trip, for which the official Leapmotor app states 77 km / 0.3 kWh / **4.9 L** — the cloud's
    per-trip record agrees with the app to the decimal and Mate's tank arithmetic reads **3.886 L**,
    20.7 % lower. Mate's figure is not sloppy: the car reported the tank level 145 times inside that
    drive at millilitre resolution, and the last reading before it and the first after give the
    identical 3.886, so nothing is lost at the edges and nothing to a coarse gauge. Two of the car's
    own measurements disagree and this module cannot say which is physically right — but the app
    shows 4.9, so that is the figure Mate has to agree with, or every owner reads a fifth off every
    total and reports it as our defect.

    An INDEX and not an annotation on purpose: the litres are decided in exactly one place
    (`db_reader._reev_trip_fuel`), which five aggregates already go through. Returning a per-trip
    figure for that one reader to prefer keeps the trips list, the trip detail, the Overview card,
    the period card and the temperature chart on one answer by construction — a card reading 3.9 L
    over a list of drives adding up to 4.9 is the defect this shape exists to make impossible.

    ⚠️ `driveReevOil` ABSENT is not `driveReevOil` 0.0: 97 of the 98 records in his bundle read 0.0
    and mean it (he drives on the battery), while a record without the field is no answer and leaves
    the tank's. Absent trips are simply not in the dict — `.get(id)` returning None means "no cloud
    answer", and a 0.0 in it means "the cloud says none was burned".

    Read-only: `trips.fuel_used_l` in the database is untouched. Matching uses the same safeguards
    and the same `_TripIndex` as `select_energy`.
    """
    vehicles, reev_ids = _cars_of_kind(db, reev=True)
    if not reev_ids:
        return {}
    raw = {
        r["id"]: dict(r) for r in db.execute("SELECT * FROM trips WHERE ended_at IS NOT NULL")
        if r["vehicle_id"] in reev_ids
    }
    if not raw:
        return {}
    bounds = {key: (_time(r["started_at"]), _time(r["ended_at"])) for key, r in raw.items()}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "api_lab_cloud_history_records" not in tables:
        return {}
    records, conflicts = {}, set()
    for row in db.execute("SELECT payload_json FROM api_lab_cloud_history_records"
                          " WHERE kind='mileage'"):
        try:
            record = json.loads(row[0])
            vin = record["vin"]
            start = _cloud_time(record.get("routeStartTs"))
            end = _cloud_time(record.get("routeEndTs"))
            if start is None or end is None or end <= start:
                continue
            # `"driveReevOil" in record` FIRST: _number turns an absent field and a present
            # unreadable one into the same None, and only the second is a reason to distrust the
            # record rather than simply have no fuel from it.
            oil = _number(record.get("driveReevOil")) if "driveReevOil" in record else None
            distance = _number(record.get("totalMileage"))
            # 🔴 A record that describes NOTHING — no distance and no litres — is dropped before it
            # can make a drive ambiguous. The cloud files these constantly: **41 of the 98 records**
            # in @ebagnoli's real history carry 0 km, none of them carries fuel, and one of them sits
            # across the tail of the 19/09 drive — the single trip in that bundle whose figure the
            # owner can check against his own app. The ambiguity rule refused both, so the calibrated
            # trip matched nothing and this whole file did precisely nothing on his install. A seeded
            # database invents no such records and matched cleanly, which is why this was only found
            # by rebuilding his own data.
            #
            # ⚠️ No distance AND no litres, not no distance. 0 km with fuel on it is the generator
            # charging a parked car: that fuel is real — Mate only refuses to blame the DRIVING
            # distance for it (`_reev_engine_on`) — so such a record is kept, still makes the drive
            # ambiguous, and the tank answers. An honest "cannot tell" beats a silent loss.
            if not distance and not oil:
                continue
            key = (vin, start, end)
            value = (oil, distance)
            if key in records and records[key] != value:
                conflicts.add(key)
            records[key] = value
        except (TypeError, ValueError, KeyError):
            continue
    kept = _assign_records(_TripIndex(raw, bounds, vehicles), records, conflicts, raw, bounds)
    # A trip's litres are the sum of its matched pieces, and nothing as soon as one piece has no
    # figure: half a drive's petrol is not the drive's.
    per_trip = {}
    for trip_id, pieces in kept.items():
        litres = [records[key][0] for _, _, key in pieces]
        if not any(v is None for v in litres):
            per_trip[trip_id] = sum(litres)
    # A merged drive is the sum of its segments, or nothing — the same rule `select_energy` applies
    # to the energy, and for the same reason: the parent row alone is the first segment only.
    children = _segments_of(raw)
    for parent in [p for p in children if p in raw]:
        # Transitively, like `select_energy`: a merged trip can itself be merged into another, and a
        # one-level walk would leave a grandchild's litres out of the total silently.
        segments = [parent]
        for segment in segments:
            for child in children.get(segment, []):
                if child not in segments:
                    segments.append(child)
        if all(seg in per_trip for seg in segments):
            per_trip[parent] = sum(per_trip[seg] for seg in segments)
        else:
            per_trip.pop(parent, None)
    return per_trip


def select_energy(db, displayed):
    """Annotate EV rows only, with conservative full-trip cloud matching.

    Cloud records may be rounded. We allow 90 seconds at the boundaries,
    require at least 80% time coverage and compatible distance, and reject
    overlapping records, conflicting versions and ambiguous local matches.
    These are matching safeguards, not claimed Leapmotor trip thresholds.

    A trip whose energy comes from the cloud also gets the car's own top speed from the
    same records (`cloud_max_speed_kmh`), or None when a record lacks it or its versions disagree.
    """
    if not displayed:
        return displayed
    # The per-car-then-account rule lives in one place now — `cloud_fuel_by_trip` asks the same
    # question of the other half of the fleet, and two copies of it would drift the day someone
    # changes how a car is recognised as a range-extender.
    vehicles, ev_ids = _cars_of_kind(db, reev=False)
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
    speeds = {}
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
                speeds.setdefault(key, set()).add(_number(record.get("maxSpeed")))
                if key in records and records[key] != value:
                    conflicts.add(key)
                records[key] = value
            except (TypeError, ValueError, KeyError):
                continue
    # The window, the bisection and the 90-second tolerance live in _TripIndex — the fuel selection
    # asks the same question of the same records, and two copies of it would drift.
    index = _TripIndex(raw, bounds, vehicles)

    assigned = {}
    assigned_keys = {}
    blocked = set()
    for key, (energy, distance) in records.items():
        vin, start, end = key
        candidates = index.candidates(vin, start, end)
        if len(candidates) != 1:
            blocked.update(candidates)
            continue
        trip_id = candidates[0]
        if key in conflicts or energy is None or distance is None:
            blocked.add(trip_id)
            continue
        assigned.setdefault(trip_id, []).append((start, end, energy, distance))
        assigned_keys.setdefault(trip_id, []).append(key)
    cloud = {}
    cloud_speed = {}
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
        known = [next(iter(speeds[key])) if len(speeds[key]) == 1 else None
                 for key in assigned_keys[trip_id]]
        cloud_speed[trip_id] = None if None in known else max(known)
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
        trip["cloud_max_speed_kmh"] = None
        if cloud_value is not None:
            top = [cloud_speed[segment] for segment in segments]
            trip["cloud_max_speed_kmh"] = None if None in top else max(top)
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
