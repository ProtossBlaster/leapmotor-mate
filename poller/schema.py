"""The database schema, and the one function that brings a file up to it.

Deliberately a module of its OWN, importing nothing from the rest of Mate. The WEB has to be able to
run this — it reads these tables and until v3.6.6 merely hoped the poller had migrated them, which
cost a 500 on the Charges page and on every trip detail for anyone whose poller had not started.

Its own module rather than an import from `db`, because `poller/db.py` pulls in `crypto` and
`geohash`, and `web/` has files by BOTH of those names — `geohash.py` differs between the two by 64
lines. Reaching into the poller from the web would have loaded the wrong ones under the right names,
and the first attempt at exactly that also put `poller/` ahead of `web/` on the path, so uvicorn
re-imported `poller/main.py` and the web would not boot at all. Nothing here imports anything, so
none of that can happen.
"""


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    id          INTEGER PRIMARY KEY,
    vin         TEXT UNIQUE NOT NULL,
    car_type    TEXT,
    year        INTEGER,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY,
    vehicle_id   INTEGER REFERENCES vehicles(id),
    recorded_at  TEXT NOT NULL,
    latitude     REAL,
    longitude    REAL,
    speed_kmh    REAL,
    odometer_km  REAL,
    soc                 REAL,
    outside_temp        REAL,
    inside_temp         REAL,
    climate_target_temp REAL,
    battery_min_temp    REAL,
    range_km            REAL,
    gear             TEXT,
    charging         INTEGER DEFAULT 0,
    is_locked        INTEGER DEFAULT NULL,
    climate_on       INTEGER DEFAULT NULL,
    climate_cooling  INTEGER DEFAULT NULL,
    climate_heating  INTEGER DEFAULT NULL,
    climate_defrost  INTEGER DEFAULT NULL,
    trunk_open       INTEGER DEFAULT NULL,
    windows_open     INTEGER DEFAULT NULL,
    sunshade_open    INTEGER DEFAULT NULL,
    plug_connected   INTEGER DEFAULT NULL,
    ready            INTEGER DEFAULT NULL,
    charge_completed INTEGER DEFAULT NULL,
    security_active  INTEGER DEFAULT NULL,
    windows_open_count INTEGER DEFAULT NULL,
    door_driver_open     INTEGER DEFAULT NULL,
    door_passenger_open  INTEGER DEFAULT NULL,
    door_rear_left_open  INTEGER DEFAULT NULL,
    door_rear_right_open INTEGER DEFAULT NULL,
    window_fl_open       INTEGER DEFAULT NULL,
    window_rl_open       INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    id                   INTEGER PRIMARY KEY,
    vehicle_id           INTEGER REFERENCES vehicles(id),
    started_at           TEXT,
    ended_at             TEXT,
    start_lat            REAL,
    start_lon            REAL,
    end_lat              REAL,
    end_lon              REAL,
    distance_km          REAL,
    start_soc            REAL,
    end_soc              REAL,
    start_odometer_km    REAL,
    end_odometer_km      REAL,
    regen_kwh            REAL DEFAULT 0,
    duration_min         REAL,
    efficiency_kwh_100km REAL,
    efficiency_soc       REAL,                    -- backup of the SoC-derived efficiency (EC override is reversible)
    ec_kwh               REAL,                    -- cloud getEC total for this trip (driving energy)
    ec_driving           REAL,
    ec_ac                REAL,
    ec_other             REAL,
    ec_tried             INTEGER DEFAULT 0,       -- EC enrichment attempts (cloud aggregation lags a fresh trip)
    ec_stable            INTEGER DEFAULT 0,       -- 1 once the cloud EC stabilised (two equal reads) → stop re-fetching
    merged_into_id       INTEGER DEFAULT NULL,
    note                 TEXT,                    -- #107: optional free-text user note (traffic, weather, road…)
    drive_mode           TEXT,                    -- #107: manual tag — 'comfort' / 'normal' / 'sport' (not in cloud)
    one_pedal            INTEGER                  -- #107: manual tag — 1 on / 0 off / NULL not set (not in cloud)
);

CREATE TABLE IF NOT EXISTS trip_positions (
    id          INTEGER PRIMARY KEY,
    trip_id     INTEGER REFERENCES trips(id),
    recorded_at TEXT NOT NULL,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    speed_kmh   REAL,
    soc         REAL
);

CREATE TABLE IF NOT EXISTS charges (
    id               INTEGER PRIMARY KEY,
    vehicle_id       INTEGER REFERENCES vehicles(id),
    started_at       TEXT,
    ended_at         TEXT,
    start_soc        REAL,
    end_soc          REAL,
    energy_added_kwh REAL,
    duration_min     REAL,
    latitude         REAL,
    longitude        REAL,
    charge_type      TEXT DEFAULT 'AC',        -- AC / DC (from power level)
    location_type    TEXT DEFAULT NULL,         -- HOME / AC / FAST / HPC (user-set)
    max_power_kw     REAL,
    cost             REAL,
    ac_energy_kwh    REAL,         -- wallbox energy a HOME charge is billed on = sum of the counter's rises
    wallbox_energy_start_kwh REAL, -- last wallbox counter reading seen (running baseline for that sum)
    wb_stuck_kwh     REAL,         -- #215: kWh the CAR reported drawing while the counter never moved
    gross_kwh        REAL,         -- #222: kWh the CHARGER says it delivered, TYPED BY THE OWNER.
                                   -- Never measured by Mate and never mixed with the measured
                                   -- figures: it prices the charge (like a wallbox meter does at
                                   -- home) and shows the conversion loss. The energy Mate reports
                                   -- and totals stays the battery (DC) one — see _billed_kwh.
    note             TEXT,         -- #107: optional free-text user note (location, shade, weather…)
    merged_into_id   INTEGER DEFAULT NULL  -- user merge: a child charge points at its parent. The
                                   -- car declares the CABLE GONE the instant the current stops, so
                                   -- one plug-in comes back as several rows (beta #29: a single
                                   -- 30-second frame; a load-balancing wallbox: six rows from one
                                   -- night). A grace window would have to guess how long a real
                                   -- pause lasts, and a closed charge is never recomputed — so the
                                   -- user joins the rows instead, and can split them again.
);

CREATE TABLE IF NOT EXISTS maintenance_logs (
    id               INTEGER PRIMARY KEY,
    vehicle_id       INTEGER REFERENCES vehicles(id),
    service_type     TEXT NOT NULL,             -- matches a pack item's service_type
    done_date        TEXT NOT NULL,             -- ISO date the service was performed
    done_odometer_km REAL,                       -- odometer at the service (prefilled with current)
    note             TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_positions_vehicle ON positions(vehicle_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_trip_positions_trip ON trip_positions(trip_id);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id, started_at);
CREATE INDEX IF NOT EXISTS idx_charges_vehicle ON charges(vehicle_id, started_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_vehicle ON maintenance_logs(vehicle_id, service_type);
-- Charge/Wallbox queries (power curve, time-of-use cost split, "has power" EXISTS)
-- filter charging=1 and range/scan recorded_at; a small partial index keeps them
-- fast as `positions` grows to millions of rows (~8% of rows are charging=1).
CREATE INDEX IF NOT EXISTS idx_positions_charging_recorded ON positions(recorded_at) WHERE charging = 1;

-- Research / BetaTester mode only (MateBetaTesterOnly build). Full raw-signal history (delta:
-- one row per signal that changed value), plus the tester's logbook. Empty/unused in the
-- normal build. Pruned by retention so it can't grow unbounded.
CREATE TABLE IF NOT EXISTS raw_signals_log (
    id          INTEGER PRIMARY KEY,
    vehicle_id  INTEGER,
    ts          INTEGER NOT NULL,   -- epoch ms (signal timestamp)
    sig_key     TEXT NOT NULL,      -- raw Leapmotor signal id, e.g. "3235"
    value       TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_signals_ts ON raw_signals_log(ts);

CREATE TABLE IF NOT EXISTS research_logbook (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,   -- epoch ms the note was added
    note        TEXT NOT NULL       -- e.g. "engine started to charge while driving", "refueled to 100%"
);

-- Daily ledger of the car's OFFICIAL lifetime counters (cloud mileage/energy/detail: totalEnergy
-- includes parked/standby, integer kWh) plus the getEC driving split over the window since the
-- previous snapshot. Δ between two rows = total consumption incl. parked, error ≤ ±1 kWh at the
-- window edges REGARDLESS of span (counter sampling — errors don't accumulate); Δ − getEC = the
-- parked/standby share. Raw readings, stored as served, never corrected in place: counter resets
-- and cloud gaps are handled at READ time (total_increasing-style). Silent phase-1 collector, no
-- UI yet.
CREATE TABLE IF NOT EXISTS energy_counter_snapshots (
    id                INTEGER PRIMARY KEY,
    vin               TEXT NOT NULL,
    taken_at          TEXT NOT NULL,     -- UTC ISO
    total_energy_kwh  INTEGER,           -- lifetime consumption counter, integer kWh as served
    total_mileage_km  REAL,              -- from the 0.1-mile field ×1.609344 (finer than the km int)
    ec_driving_kwh    REAL,              -- getEC over [previous snapshot's taken_at, taken_at]
    ec_ac_kwh         REAL,
    ec_other_kwh      REAL,
    ec_status         TEXT               -- 'first' | 'ok' | 'empty' (no driving) | 'miss' (cloud gap)
);
CREATE INDEX IF NOT EXISTS idx_energy_snap_vin_taken ON energy_counter_snapshots(vin, taken_at);

-- Kilometres the car covered while the cloud had nothing new to say. They belong to no trip: the
-- silence may hold the tail of one drive, a night's parking and the start of another, and nothing
-- in the data says how it divides. So they are recorded HERE rather than welded onto whichever
-- trip opens next, and stay out of distances, consumption and costs — both halves of the fraction
-- together, or the consumption figures get worse instead of better.
CREATE TABLE IF NOT EXISTS offline_gaps (
    id             INTEGER PRIMARY KEY,
    vehicle_id     INTEGER NOT NULL,
    started_at     TEXT NOT NULL,     -- UTC ISO: when the cloud last had NEWS (not the last poll)
    ended_at       TEXT NOT NULL,     -- UTC ISO: when it spoke again
    odometer_start REAL,
    odometer_end   REAL,
    distance_km    REAL NOT NULL,
    soc_start      REAL,
    soc_end        REAL,
    energy_kwh     REAL               -- ΔSoC × the capacity in force then; 0 when the SoC rose
);
CREATE INDEX IF NOT EXISTS idx_offline_gaps_vehicle ON offline_gaps(vehicle_id, started_at);
"""


def ensure_schema(conn) -> None:
    """Create/alter everything Mate's tables need, and NOTHING else.

    Lifted out of Database.__init__ so the WEB can run it too. The schema is the poller's, and the
    web merely hoped it had run: v3.6.6 added `charges.gross_kwh`, the web named it in five queries,
    and any install whose poller had not started yet answered 500 on the Charges page and on every
    trip detail. Guarding that one column fixed that one column; this is what stops the next one.

    Deliberately NOT the rest of __init__ — the constructor also runs eleven data repairs, deletes
    phantom rows and migrates the secrets. Those belong to the process that owns the data; a reader
    must not do them, least of all concurrently with the poller doing the same.

    Idempotent by construction (every step is `IF NOT EXISTS` or `if column not in ...`) and cheap:
    a handful of PRAGMAs on a database that is already up to date."""
    conn.executescript(SCHEMA)
    # migration: add battery_min_temp if missing (existing DBs)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "climate_target_temp" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN climate_target_temp REAL")
    if "battery_min_temp" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN battery_min_temp REAL")
    if "is_locked" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN is_locked INTEGER DEFAULT NULL")
    if "climate_on" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN climate_on INTEGER DEFAULT NULL")
    if "climate_cooling" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN climate_cooling INTEGER DEFAULT NULL")
    if "climate_heating" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN climate_heating INTEGER DEFAULT NULL")
    if "climate_defrost" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN climate_defrost INTEGER DEFAULT NULL")
    if "trunk_open" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN trunk_open INTEGER DEFAULT NULL")
    if "windows_open" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN windows_open INTEGER DEFAULT NULL")
    if "sunshade_open" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN sunshade_open INTEGER DEFAULT NULL")
    if "plug_connected" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN plug_connected INTEGER DEFAULT NULL")
    if "remaining_charge_min" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN remaining_charge_min INTEGER DEFAULT NULL")
    if "charge_voltage_v" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN charge_voltage_v REAL DEFAULT NULL")
    if "charge_current_a" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN charge_current_a REAL DEFAULT NULL")
    if "ready" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN ready INTEGER DEFAULT NULL")
    if "charge_completed" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN charge_completed INTEGER DEFAULT NULL")
    if "security_active" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN security_active INTEGER DEFAULT NULL")
    if "windows_open_count" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN windows_open_count INTEGER DEFAULT NULL")
    # Per-door + left-side window state (the live Overview car image; the poller already computes
    # these — see car_image.py). Names are fixed literals, never user input.
    for _c in ("door_driver_open", "door_passenger_open", "door_rear_left_open",
               "door_rear_right_open", "window_fl_open", "window_rl_open"):
        if _c not in cols:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {_c} INTEGER DEFAULT NULL")
    # migration: AC-port / V2L mode (signal 47). 0 idle / 1 AC charging / 2 V2L discharge. Lets the
    # V2L monitor read per-poll mode AND lets get_vampire_drain EXCLUDE V2L periods (a parked V2L
    # discharge must NOT be counted as standby/vampire drain).
    if "ac_port_mode" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN ac_port_mode INTEGER DEFAULT NULL")
    # migration: extended climate panel (validated on-car 2026-06-20) — fan level (1941 acAirVolume,
    # 1-7), recirculation (1943: 1=recirc / 0=fresh), base climate mode (3713: 0 auto/1 cool/3 heat/4 vent).
    if "fan_level" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN fan_level INTEGER DEFAULT NULL")
    if "recirculation" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN recirculation INTEGER DEFAULT NULL")
    if "climate_mode" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN climate_mode INTEGER DEFAULT NULL")
    # migration: REEV dual-energy for the live Overview — fuel tank level % (3235), range on fuel
    # alone (3259), and combined battery+fuel range (3261). All NULL on a BEV; the status card shows
    # them only when present (range-extender models). range_km stays the EV-only range (3260).
    for _c in ("fuel_level_pct", "fuel_range_km", "combined_range_km"):
        if _c not in cols:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {_c} REAL DEFAULT NULL")
    # migration: the litres the car itself counts (signal 3263, millilitres → litres). Everything
    # in litres was until now a percentage multiplied by an ASSUMED tank size, and the assumption
    # was wrong on the C10 (47.5 L, not 50) — so every litre figure a C10 owner ever saw was 5 %
    # high. With this the car does the counting. NULL on a BEV and on every row written before.
    if "fuel_liters" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN fuel_liters REAL DEFAULT NULL")
    # migration: the CAR's own timestamp on the frame this row came from (signal sts, or 1) — #178.
    # `recorded_at` is when MATE wrote the row, which is always a few seconds ago; it says nothing
    # about how old the data inside it is. When the car can't reach the cloud, the cloud keeps
    # re-serving the last frame it got, so the row is new and its contents are not. Storing the
    # frame's own time is what lets the web tell those two apart. NULL where the car doesn't
    # report it (and on every row written before this column existed).
    if "frame_ts" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN frame_ts INTEGER DEFAULT NULL")
    # migration: the car's DECLARED ability codes (VehicleAbility ints, stored as a JSON list) —
    # lets the diagnostic + future capability-gating show ONLY what a model actually supports,
    # instead of assuming every car has the same commands (#67; also covers models we don't own
    # yet, e.g. the B05). Refreshed by ensure_vehicle on every poller start.
    vcols = {r[1] for r in conn.execute("PRAGMA table_info(vehicles)").fetchall()}
    if "abilities" not in vcols:
        conn.execute("ALTER TABLE vehicles ADD COLUMN abilities TEXT DEFAULT NULL")
    # migration: PER-VEHICLE battery capacity (usable + as-new nominal for SoH). Capacity is a
    # vehicle attribute — energy is written as ΔSoC×capacity — so with >1 car per account each
    # needs its OWN (a B10's ~65 kWh vs a T03's ~36 kWh is ~80% off, and it corrupts the STORED
    # trip/charge energy, not just the display). _backfill_vehicle_capacity() then seeds existing
    # rows from the legacy global so a single-car install stays byte-identical.
    if "capacity_kwh" not in vcols:
        conn.execute("ALTER TABLE vehicles ADD COLUMN capacity_kwh REAL DEFAULT NULL")
    if "capacity_nominal_kwh" not in vcols:
        conn.execute("ALTER TABLE vehicles ADD COLUMN capacity_nominal_kwh REAL DEFAULT NULL")
    # migration: per-charge wallbox AC energy (the "wallbox, to pay" figure) on existing DBs
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(charges)").fetchall()}
    if "ac_energy_kwh" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN ac_energy_kwh REAL")
    if "wallbox_energy_start_kwh" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN wallbox_energy_start_kwh REAL")
    # migration: #215 — energy the car reported drawing while the wallbox counter stood still
    if "wb_stuck_kwh" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN wb_stuck_kwh REAL")
    # migration: #222 — the charger's own kWh, typed in for a public charge
    if "gross_kwh" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN gross_kwh REAL")
    # migration: flag charges reconstructed from a SoC jump (car was asleep/offline to the
    # cloud during the charge, so it was never seen live — recorded from the SoC delta instead).
    if "reconstructed" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN reconstructed INTEGER DEFAULT 0")
    # migration: public charging-station label, resolved by the web layer from OSM
    # (web/charger_locator.py; '' = looked up, nothing found). Display-only — it never
    # feeds charge detection, costs or the HOME/AC/FAST/HPC location_type.
    if "location_name" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN location_name TEXT DEFAULT NULL")
    # migration: link back to the label's source page (openstreetmap.org / openchargemap.org),
    # when the winning candidate had one (web/charger_locator.py _osm_url / OCM's poi/details
    # URL). Display-only, like location_name — NULL on charges labelled before this column
    # existed, until they're re-swept or manually recalculated (📍 button).
    if "location_url" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN location_url TEXT DEFAULT NULL")
    # migration: #107 — optional free-text user note on a charge (station location, shade,
    # reliability, parking, weather, personal remarks). Display/context only, never computed on.
    if "note" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN note TEXT")
    # migration: #120 — mark a HOME charge as FREE (e.g. self-produced solar, or any free home
    # charge). The charge KEEPS its Home location (stays on the Home side of the Home-vs-Public
    # split) but its cost is pinned to 0 and protected from every recompute (compute_cost returns
    # 0 when is_free). "Free-away" stays the FREE location_type — this flag is HOME-only.
    if "is_free" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN is_free INTEGER DEFAULT 0")
    # migration: #188 — mark a charge the user TYPED IN (the "add a past charge" form or the CSV
    # import) as opposed to one the poller measured. location_type='MANUAL' cannot answer this:
    # it doubles as the COST BASIS someone picks to type the price of a real charge, so an edit
    # form gated on it would hand out the times and SoC of measured sessions to be rewritten.
    # Existing rows are backfilled from the signature a typed-in charge has always carried — the
    # MANUAL basis plus no telemetry whatsoever (the poller fills lat/lon at charge start and
    # duration/peak power at the end, and a reconstructed charge gets both too).
    if "manual_entry" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN manual_entry INTEGER DEFAULT 0")
        conn.execute(
            "UPDATE charges SET manual_entry = 1 "
            "WHERE location_type = 'MANUAL' AND COALESCE(reconstructed, 0) = 0 "
            "AND latitude IS NULL AND longitude IS NULL "
            "AND duration_min IS NULL AND max_power_kw IS NULL AND ac_energy_kwh IS NULL")
    # migration: #237 — the car's own odometer at the moment the charge started. Written by the
    # poller from the same frame that opens the charge, TYPED by the owner on a charge they add by
    # hand, and back-filled once from `positions` for sessions already in the DB (see
    # Database._backfill_charge_odometer — measured 26 of 28 recoverable on a real B10, to the
    # second, because both rows come from the SAME poll).
    #
    # 🔑 Why a stored column rather than a lookup: `positions` is prunable
    # (positions_retention_days), so deriving it on the fly would quietly lose the odometer of
    # every older charge on any install that prunes. And it is the ONLY way a charge from before
    # Mate existed can carry kilometres at all — no poll of it was ever made.
    if "odometer_km" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN odometer_km REAL")
    # migration: manual charge-merge link — twin of the trips one below. A child charge points at
    # the parent it was merged into; the merge writes ONLY this column, so unmerging restores the
    # original rows exactly. See the CREATE TABLE above for why the rows arrive split.
    if "merged_into_id" not in ccols:
        conn.execute("ALTER TABLE charges ADD COLUMN merged_into_id INTEGER DEFAULT NULL")
    # migration: manual trip-merge link — a child trip points to the parent it was merged into
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(trips)").fetchall()}
    if "merged_into_id" not in tcols:
        conn.execute("ALTER TABLE trips ADD COLUMN merged_into_id INTEGER DEFAULT NULL")
    # migration: flag trips RECONSTRUCTED from an odometer jump (car offline/asleep to the cloud —
    # or poller down — for the whole drive, so no DRIVING poll ever fired). Twin of charges.reconstructed.
    if "reconstructed" not in tcols:
        conn.execute("ALTER TABLE trips ADD COLUMN reconstructed INTEGER DEFAULT 0")
    # migration: per-trip EC (driving) energy split from the cloud getEC endpoint (Phase 2).
    # efficiency_soc backs up the original SoC-derived efficiency so the EC override is fully
    # reversible; ec_tried counts enrichment attempts (cloud aggregation lags a fresh trip).
    for _c, _t in (("efficiency_soc", "REAL"), ("ec_kwh", "REAL"), ("ec_driving", "REAL"),
                   ("ec_ac", "REAL"), ("ec_other", "REAL"), ("ec_tried", "INTEGER DEFAULT 0"),
                   ("ec_stable", "INTEGER DEFAULT 0")):
        if _c not in tcols:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
    # migration: #107 — per-trip user note + MANUAL driving tags. drive_mode/one_pedal are user-set
    # because the Leapmotor cloud does not expose drive mode or One-Pedal (verified on-car); they
    # explain consumption differences the raw data can't. Display/context only, never computed on.
    for _c, _t in (("note", "TEXT"), ("drive_mode", "TEXT"), ("one_pedal", "INTEGER")):
        if _c not in tcols:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
    # migration: REEV Phase C — fuel tank level % (signal 3235) at trip start/end. The drop gives
    # the fuel burned (× tank litres) → per-trip L/100km and the EV/fuel split. NULL on a BEV.
    for _c, _t in (("fuel_start_pct", "REAL"), ("fuel_end_pct", "REAL")):
        if _c not in tcols:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
    # migration: the same two ends in LITRES, straight off the car's own counter (3263) instead of
    # a percentage times an assumed tank. "× tank litres" above was the whole problem — the assumed
    # tank was 50 L for everyone and a C10's is 47.5. Where these are present the burn is measured;
    # where they aren't (a BEV, or any trip recorded before this) the percentages still answer.
    for _c, _t in (("fuel_start_l", "REAL"), ("fuel_end_l", "REAL")):
        if _c not in tcols:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
    # migration: per-trip elevation gain/loss (metres) + outside temperature (°C), looked up
    # post-trip against Open-Meteo (the Leapmotor cloud exposes neither altitude nor an ambient
    # temperature — only lat/lon and cabin temp). Per-segment like regen_kwh — a merged group's
    # total is the sum of its segments (web/db_reader.py _trip_group_stats), so merging never
    # needs re-enrichment. elev_tried/elev_done mirror ec_tried/ec_stable but need no convergence
    # logic: terrain (and a past hour's weather) is static, one successful lookup is final.
    for _c, _t in (("elevation_gain_m", "REAL"), ("elevation_loss_m", "REAL"),
                   ("outside_temp_start_c", "REAL"), ("outside_temp_end_c", "REAL"),
                   ("elev_tried", "INTEGER DEFAULT 0"), ("elev_done", "INTEGER DEFAULT 0")):
        if _c not in tcols:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
    # migration: per-POINT altitude (metres) for the trip-profile chart. Only the downsampled
    # subset the enrichment sweep actually queried Open-Meteo for gets a value here — the rest
    # stay NULL and web/db_reader.py interpolates them at read time for a smooth chart line.
    tpcols = {r[1] for r in conn.execute("PRAGMA table_info(trip_positions)").fetchall()}
    if "elevation_m" not in tpcols:
        conn.execute("ALTER TABLE trip_positions ADD COLUMN elevation_m REAL")
    # migration: geohash (7 chars ≈ 150m cell) of start/end lat-lon — the "similar trips"
    # comparator's fast pre-filter (web/db_reader.py get_similar_trips groups candidates by
    # this before validating the actual route). Set at trip creation/finalize (below) going
    # forward; _backfill_trip_geohashes fills every existing trip once, offline (pure math on
    # lat/lon already stored — unlike the auto-note's reverse-geocoding, no network call, so
    # there's no reason to defer this to a web-side sweep).
    for _c in ("start_geohash", "end_geohash"):
        if _c not in tcols:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} TEXT DEFAULT NULL")
    conn.commit()


# Everything else Database.__init__ does — eleven data repairs, dropping phantom rows, migrating the
# secrets — stays with the poller. Those belong to the process that owns the data; a reader must not
# run them, least of all at the same time as the poller.
