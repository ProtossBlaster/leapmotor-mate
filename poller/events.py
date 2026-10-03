"""The moments the car's state changed, derived from the `positions` rows the poller already stores.

Every poll stores one row of flags (lock, doors, cable, climate, READY…); this reads those rows in
the order they were written and keeps one `events` row per change that held for two consecutive
frames. A single frame saying otherwise is a blink the cloud sends often enough (a door "open" for
one poll out of hundreds) to make a list of every transition unreadable; a repeated frame — the
same `frame_ts` served again while the car sleeps — is not a second frame. The row's own time is the
event's: the second frame confirms the change, it does not move it.

The reading position and the per-kind state live in one `settings` row per car, written in the same
transaction as the events it produced, so a restart at any point neither repeats nor loses a change.
The cursor is the row id: the two writers (poller and web) both append, and a write delayed by a lock
lands after the cursor in id order where it would land before it in time order. Deleting rows lets
SQLite reuse the newest ids, so every deletion of `positions` ends with `clamp_cursors`.
"""
import json
import logging

log = logging.getLogger(__name__)

STATE_KEY = "events_state_{vehicle_id}"


def _flag(column):
    return lambda r: None if r[column] is None else int(r[column] != 0)


def _unlocked(r):
    return None if r["is_locked"] is None else int(r["is_locked"] != 1)


def _cable(r):
    """The cable in the port: the charge session, or the AC port reporting a cable on a parked car
    while the charger withholds the current (a wallbox waiting for its schedule)."""
    if r["plug_connected"] is None:
        return None
    parked = r["gear"] in (None, "P") and (r["speed_kmh"] or 0) <= 1
    return int(bool(r["plug_connected"]) or (r["ac_port_mode"] == 1 and parked))


def _v2l(r):
    return None if r["ac_port_mode"] is None else int(r["ac_port_mode"] == 2)


def _climate(r):
    return None if r["climate_on"] is None else int(r["climate_on"] == 1)


def _ready(r):
    return None if r["ready"] is None else int(r["ready"] == 1)


# kind → the state a stored row says it was in (0/1), or None when the row says nothing about it.
RULES = {
    "unlocked": _unlocked,
    "door_driver": _flag("door_driver_open"),
    "door_passenger": _flag("door_passenger_open"),
    "door_rear_left": _flag("door_rear_left_open"),
    "door_rear_right": _flag("door_rear_right_open"),
    "trunk": _flag("trunk_open"),
    "window_fl": _flag("window_fl_open"),
    "window_rl": _flag("window_rl_open"),
    "cable": _cable,
    "v2l": _v2l,
    "climate": _climate,
    "defrost": _flag("climate_defrost"),
    "rapid_heat": _flag("climate_heating"),
    "rapid_cool": _flag("climate_cooling"),
    "ready": _ready,
}

_COLUMNS = ("id, recorded_at, frame_ts, latitude, longitude, soc, odometer_km, inside_temp, "
            "climate_target_temp, outside_temp, gear, speed_kmh, is_locked, door_driver_open, "
            "door_passenger_open, door_rear_left_open, door_rear_right_open, trunk_open, "
            "window_fl_open, window_rl_open, plug_connected, ac_port_mode, "
            "climate_on, climate_defrost, climate_heating, climate_cooling, ready")


def load_state(conn, vehicle_id: int) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = ?",
                       (STATE_KEY.format(vehicle_id=vehicle_id),)).fetchone()
    state = json.loads(row[0]) if row else {}
    state.setdefault("cursor_id", 0)
    state.setdefault("last_frame_ts", None)
    state.setdefault("kinds", {})
    return state


def consume(conn, vehicle_id: int, max_rows=5000) -> int:
    """Read this car's `positions` rows after the cursor, at most `max_rows` of them, and store the
    changes that held for two frames. Returns the number of rows read; a full batch means there is
    history still to read, and the next call carries on from where this one stopped. The state says
    whether the history has been read through (`caught_up`) and, until then, how much of it (`pct`):
    a row written since, by the web too, is the next round's, not history."""
    state = load_state(conn, vehicle_id)
    sql = f"SELECT {_COLUMNS} FROM positions WHERE vehicle_id = ? AND id > ? ORDER BY id"
    args = [vehicle_id, state["cursor_id"]]
    if max_rows is not None:
        sql += " LIMIT ?"
        args.append(max_rows)
    rows = conn.execute(sql, args).fetchall()
    if not rows:
        if not state.get("caught_up"):              # the last batch was full and nothing came after it
            state["caught_up"], state["pct"] = True, 100
            state.pop("catch_up", None)
            with conn:
                _save_state(conn, vehicle_id, state)
        return 0
    kinds = state["kinds"]
    events = []
    for r in rows:
        state["cursor_id"] = r["id"]
        if r["frame_ts"] is not None:
            if r["frame_ts"] == state["last_frame_ts"]:
                continue
            state["last_frame_ts"] = r["frame_ts"]
        for kind, rule in RULES.items():
            value = rule(r)
            if value is None:
                continue
            k = kinds.get(kind)
            if k is None:
                kinds[kind] = {"confirmed": value, "pending": None, "first": None}
                continue
            if value == k["confirmed"]:
                k["pending"] = k["first"] = None
                continue
            if k["pending"] != value:
                k["pending"] = value
                k["first"] = {"at": r["recorded_at"], "frame_ts": r["frame_ts"], "lat": r["latitude"],
                              "lon": r["longitude"], "soc": r["soc"], "odo": r["odometer_km"],
                              "inside": r["inside_temp"], "target": r["climate_target_temp"],
                              "outside": r["outside_temp"]}
                continue
            k["confirmed"], first = value, k["first"]
            k["pending"] = k["first"] = None
            events.append((vehicle_id, kind, first["at"], first["frame_ts"], value, first["lat"],
                           first["lon"], first["soc"], first["odo"], first["inside"],
                           first["target"], first["outside"]))
    full = max_rows is not None and len(rows) >= max_rows
    state["caught_up"], state["pct"] = not full, 100
    if full:
        if "catch_up" not in state:                 # it begins: the rows left, counted once
            state["catch_up"] = {"done": 0, "total": len(rows) + conn.execute(
                "SELECT COUNT(*) FROM positions WHERE vehicle_id = ? AND id > ?",
                (vehicle_id, state["cursor_id"])).fetchone()[0]}
        progress = state["catch_up"]
        progress["done"] += len(rows)
        state["pct"] = min(100, 100 * progress["done"] // progress["total"])
        log.info("%d rows read, %d kept, %d %% of history", len(rows), len(events), state["pct"])
    else:
        state.pop("catch_up", None)
    with conn:
        conn.executemany(
            "INSERT INTO events (vehicle_id, kind, at, frame_ts, state, latitude, longitude, soc,"
            " odometer_km, inside_temp, climate_target_temp, outside_temp)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", events)
        _save_state(conn, vehicle_id, state)
    return len(rows)


def _save_state(conn, vehicle_id: int, state: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (STATE_KEY.format(vehicle_id=vehicle_id), json.dumps(state)))


def prune(conn, vehicle_id: int, cutoff: str) -> int:
    """Drop this car's spans that ended before `cutoff`, as pairs: a span that crosses it keeps its
    start, an open span keeps its start and later its end, so nothing in the kept period reopens or
    goes missing. The neighbour is the next row of the kind in write order, as the page reads it.
    Returns the rows deleted."""
    # The CTE sits inside the subquery: a statement that starts with WITH reports no row count.
    return conn.execute(
        """DELETE FROM events WHERE id IN (
                WITH e AS (SELECT id, at, state, kind,
                                  LEAD(at) OVER w AS next_at, LAG(at) OVER w AS prev_at
                           FROM events WHERE vehicle_id = ? WINDOW w AS (PARTITION BY kind ORDER BY id))
                SELECT id FROM e WHERE at < ? AND ((state = 1 AND next_at < ?)
                                                   OR (state = 0 AND COALESCE(prev_at, at) < ?)))""",
        (vehicle_id, cutoff, cutoff, cutoff)).rowcount


def clamp_cursors(conn) -> None:
    """Pull every car's cursor back to its newest remaining row. Called by whatever deletes
    `positions`, in the same transaction: SQLite reuses the ids of deleted newest rows, and a cursor
    left beyond them would skip the rows written next."""
    for key, value in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'events_state_%'"
                                   ).fetchall():
        state = json.loads(value)
        vehicle_id = int(key[len("events_state_"):])
        newest = conn.execute("SELECT COALESCE(MAX(id), 0) FROM positions WHERE vehicle_id = ?",
                              (vehicle_id,)).fetchone()[0]
        if state.get("cursor_id", 0) > newest:
            state["cursor_id"] = newest
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (json.dumps(state), key))
