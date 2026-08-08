"""Add a SECOND vehicle to a demo DB, deliberately built so that any cross-vehicle leak is
impossible to miss:

  * car 2 lives in ROME, car 1 in MILAN            → a leak on the map is a cluster 480 km away
  * every car-2 row is MORE RECENT than every car-1 row
                                                    → any unscoped "latest" query returns car 2
  * car 2's numbers are absurd next to car 1's (13% vs 64% SoC, 88 888 km vs 12 765,
    25.4 kWh/100km vs ~15, a 199 € charge)          → a leak into an average or a total shifts it
    visibly rather than subtly

Run against a DB already seeded by poller/seed_demo.py. Read-only w.r.t. the live install:
it only ever writes the demo file it is pointed at.
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DB = sys.argv[1]
VIN2 = "LFZDEMO0MATE000CAR2"
ROME = (41.9028, 12.4964)          # Colosseo — car 2 never leaves Rome
ROME_B = (41.8340, 12.4750)        # EUR
CAP2 = 36.5                        # T03 pack, vs the B10's 65 kWh

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

if c.execute("SELECT COUNT(*) FROM vehicles WHERE vin = ?", (VIN2,)).fetchone()[0]:
    print("car 2 already present — nothing to do")
    sys.exit(0)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


# Car 1's newest sample: everything below is placed AFTER it.
last1 = c.execute("SELECT MAX(recorded_at) FROM positions WHERE vehicle_id = 1").fetchone()[0]
base = datetime.fromisoformat(last1) + timedelta(hours=1)

c.execute("INSERT INTO vehicles (id, vin, car_type, year, capacity_kwh) VALUES (2,?,'T03',2024,?)",
          (VIN2, CAP2))

# ── positions ────────────────────────────────────────────────────────────────
# 60 samples over the last 30 h, all newer than anything car 1 has.
for i in range(60):
    t = base + timedelta(minutes=30 * i)
    soc = 13.0 + (i % 7)
    c.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, speed_kmh,"
        " odometer_km, soc, range_km, gear, charging, is_locked, climate_on, plug_connected,"
        " windows_open, windows_open_count, trunk_open, security_active, battery_min_temp,"
        " outside_temp, inside_temp) VALUES (2,?,?,?,0,?,?,?, 'P',0,1,0,0,0,0,0,1,21.0,31.0,29.0)",
        (iso(t), ROME[0] + i * 0.0004, ROME[1] + i * 0.0004, 88888 + i, soc, round(soc * 2.1)))

# ── trips ────────────────────────────────────────────────────────────────────
# Wildly inefficient short hops, so a leak into "average consumption" is obvious.
for i in range(6):
    dep = base + timedelta(hours=2 + i * 4)
    arr = dep + timedelta(minutes=25)
    c.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat,"
        " end_lon, distance_km, start_soc, end_soc, start_odometer_km, end_odometer_km,"
        " regen_kwh, duration_min, efficiency_kwh_100km) VALUES (2,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (iso(dep), iso(arr), ROME[0], ROME[1], ROME_B[0], ROME_B[1],
         9.9, 30.0 - i, 26.0 - i, 88888 + i * 10, 88898 + i * 10, 0.4, 25.0, 25.4))
    tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    for k in range(6):
        tt = dep + timedelta(minutes=5 * k)
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude,"
                  " speed_kmh, soc) VALUES (?,?,?,?,?,?)",
                  (tid, iso(tt), ROME[0] + (ROME_B[0] - ROME[0]) * k / 5,
                   ROME[1] + (ROME_B[1] - ROME[1]) * k / 5, 30, 30.0 - k))

# ── charges ──────────────────────────────────────────────────────────────────
# One "home" charge in Rome (so the learned-wallbox logic has a car-2 opinion too) and one
# absurdly expensive public one.
for i, (loc, kind, kwh, cost, ac, where) in enumerate([
        ("HOME", "AC", 20.0, 7.40, 22.2, ROME),
        (None,   "DC", 28.0, 199.00, None, ROME_B)]):
    st = base + timedelta(hours=6 + i * 8)
    c.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, duration_min, latitude, longitude, charge_type, location_type,"
        " max_power_kw, cost, ac_energy_kwh) VALUES (2,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (iso(st), iso(st + timedelta(hours=2)), 13.0, 68.0, kwh, 120,
         where[0], where[1], kind, loc, 50, cost, ac))

# ── maintenance ──────────────────────────────────────────────────────────────
c.execute("INSERT INTO maintenance_logs (vehicle_id, service_type, done_date, done_odometer_km,"
          " note) VALUES (2,'tyres','2026-07-01',88000,'CAR 2 — must never show under car 1')")

con.commit()
n = {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE vehicle_id=2").fetchone()[0]
     for t in ("positions", "trips", "charges", "maintenance_logs")}
print(f"car 2 (T03, Rome) added: {n}")
print("newest row car 1:", last1)
print("newest row car 2:", c.execute(
    "SELECT MAX(recorded_at) FROM positions WHERE vehicle_id=2").fetchone()[0])
con.close()
