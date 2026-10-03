#!/usr/bin/env python3
"""Seed a self-contained DEMO database for Mate (MATE_DEMO mode).

Generates ~30 days of realistic, internally-consistent usage for a fictional
Leapmotor B10 owner based near Bologna: weekday commutes, cheap overnight home
AC charging on a TOU off-peak band, and one "weekend al mare" to Rimini with an
expensive DC HPC charge — so every page (Overview, Trips, Charges, Costs/WAC,
Statistics, Monthly report, Battery SoH, Vampire drain, Wallbox) is populated.

No real account, car or cloud. Dates are anchored to "now" at seed time, so the
demo always shows the last ~30 days. Re-run regenerates from scratch.

Run:  DB_PATH=/data/demo.db python /app/poller/seed_demo.py
"""
import json
import math
import os
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import Database, haversine_km  # noqa: E402

DB_PATH = os.environ.get("DB_PATH", "/data/demo.db")
CAP = 65.0                      # usable kWh (B10 Pro Max)
random.seed(7)                  # reproducible → stable screenshots

# ── Neutral geography (NOT the maintainer's home) ────────────────────────────
HOME = (44.4949, 11.3426)       # Bologna
WORK = (44.5632, 11.2280)       # ~14 km NW
RIMINI = (44.0594, 12.5683)     # "weekend al mare"
HPC = (44.1390, 12.2430)        # A14 service area (return-leg fast charge)

COMMUTE_WP = [HOME, (44.5180, 11.3050), (44.5400, 11.2650), WORK]
SEA_WP = [HOME, (44.4200, 11.5500), (44.3300, 11.8200), (44.2200, 12.0500),
          HPC, (44.1050, 12.3900), RIMINI]

EFF_CITY = 16.5                 # kWh/100km
EFF_HWY = 18.5


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _seg_track(wps, n):
    """n points walked evenly along the polyline wps, with small lateral jitter."""
    segs = [(wps[i], wps[i + 1]) for i in range(len(wps) - 1)]
    seg_len = [haversine_km(a[0], a[1], b[0], b[1]) for a, b in segs]
    total = sum(seg_len) or 1.0
    out = []
    for k in range(n):
        d = total * k / max(1, n - 1)
        acc = 0.0
        for (a, b), L in zip(segs, seg_len):
            if acc + L >= d or (a, b) == segs[-1]:
                t = (d - acc) / L if L else 0.0
                lat = a[0] + (b[0] - a[0]) * t
                lon = a[1] + (b[1] - a[1]) * t
                if 0 < k < n - 1:
                    lat += random.uniform(-0.004, 0.004)
                    lon += random.uniform(-0.004, 0.004)
                out.append((lat, lon))
                break
            acc += L
    return out


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    for ext in ("-wal", "-shm"):
        if os.path.exists(DB_PATH + ext):
            os.remove(DB_PATH + ext)
    db = Database(DB_PATH)
    c = db._conn

    c.execute("INSERT INTO vehicles (id, vin, car_type, year) VALUES (1, ?, 'B10', 2025)",
              ("LFZDEMO0MATE000DEMO",))
    c.execute("INSERT INTO charging_places (vehicle_id, name, latitude, longitude, radius_m, rate) "
              "VALUES (1, 'Casa', ?, ?, 100, 0.25)", HOME)

    bands = [{"start": "07:00", "end": "23:00",
              "prices": {"HOME": 0.35, "AC": 0.45, "FAST": 0.55, "HPC": 0.69},
              "days": []}]
    settings = {
        "setup_complete": "1",
        "demo_mode": "1",
        "language": "en",
        "unit_system": "metric",
        "leapmotor_user": "demo@leapmotor-mate.app",
        "battery_capacity_kwh": "65.0",
        "battery_capacity_nominal_kwh": "67.1",
        "cost_mode": "tou",
        "tou_method": "split",
        "tou_bands": json.dumps(bands),
        "price_home_kwh": "0.25",   # off-peak / off-band
        "price_ac_kwh": "0.45",
        "price_fast_kwh": "0.55",
        "price_hpc_kwh": "0.69",
        "currency": "€",
        "wallbox_auto_home": "1",
        "wallbox_enabled": "1",
    }
    for k, v in settings.items():
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Bundle the generic B10 model image so /api/car-picture serves it (no cloud in demo).
    _src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_car.png")
    _dst = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "car_picture.png")
    if os.path.exists(_src):
        try:
            shutil.copyfile(_src, _dst)
        except OSError:
            pass

    # Bundle the layer package too → /api/car-picture composes the LIVE image (charge-cable
    # animation / doors / windows) in the demo, not just the static render. Falls back to the
    # static one above if the package isn't shipped.
    _psrc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_car_pkg.zip")
    _pdst = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "car_picture_pkg.zip")
    if os.path.exists(_psrc):
        try:
            shutil.copyfile(_psrc, _pdst)
        except OSError:
            pass

    # ── state ────────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=30)).replace(hour=6, minute=30, second=0, microsecond=0)
    state = {"odo": 11840.0, "soc": 78.0, "t": start}

    def pos(dt, lat, lon, **kw):
        d = {"speed_kmh": 0, "charging": 0, "is_locked": 1, "climate_on": 0,
             "gear": "P", "plug_connected": 0, "charge_voltage_v": None,
             "charge_current_a": None, "remaining_charge_min": None,
             "charge_completed": 0, "windows_open": 0, "windows_open_count": 0,
             "trunk_open": 0, "security_active": 1, "battery_min_temp": 17.0,
             "outside_temp": round(random.uniform(8, 24), 1),
             "inside_temp": round(random.uniform(16, 22), 1), "climate_target_temp": 21.0}
        d.update(kw)
        c.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, speed_kmh, "
            "odometer_km, soc, range_km, gear, charging, is_locked, climate_on, plug_connected, "
            "charge_voltage_v, charge_current_a, remaining_charge_min, charge_completed, "
            "windows_open, windows_open_count, trunk_open, security_active, battery_min_temp, "
            "outside_temp, inside_temp, climate_target_temp) "
            "VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso(dt), lat, lon, d["speed_kmh"], round(state["odo"], 0), round(state["soc"], 1),
             round(state["soc"] * 4.2, 0), d["gear"], d["charging"], d["is_locked"], d["climate_on"],
             d["plug_connected"], d["charge_voltage_v"], d["charge_current_a"],
             d["remaining_charge_min"], d["charge_completed"], d["windows_open"],
             d["windows_open_count"], d["trunk_open"], d["security_active"], d["battery_min_temp"],
             d["outside_temp"], d["inside_temp"], d["climate_target_temp"]))

    def precondition(dep, lat, lon):
        """The cabin heated from the app before leaving: two frames, so the Events page keeps it,
        the first still cold and the last warm, so its end says what it did."""
        for minutes, cabin in ((9, 14.0), (5, 17.5), (1, 20.5)):
            pos(dep - timedelta(minutes=minutes), lat, lon, climate_on=1, inside_temp=cabin)
        pos(dep, lat, lon, inside_temp=20.5)

    def plug_in(at, lat, lon):
        """The cable in at the wallbox hours before its schedule starts the charge."""
        for minutes in (0, 3):
            pos(at + timedelta(minutes=minutes), lat, lon, plug_connected=1)

    def unload(arr, lat, lon):
        """The tailgate open for a couple of minutes after arriving."""
        for minutes in (1, 2):
            pos(arr + timedelta(minutes=minutes), lat, lon, is_locked=0, trunk_open=1)
        pos(arr + timedelta(minutes=4), lat, lon)

    def drive(dep, wps, dist_km, eff, avg_kmh, climate=False):
        e = dist_km * eff / 100.0
        dsoc = e / CAP * 100.0
        dur = dist_km / avg_kmh * 60.0
        arr = dep + timedelta(minutes=dur)
        s0, o0 = state["soc"], state["odo"]
        s1, o1 = s0 - dsoc, o0 + dist_km
        n = max(8, int(dur / 4))
        track = _seg_track(wps, n)
        c.execute(
            "INSERT INTO trips (vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, "
            "end_lon, distance_km, start_soc, end_soc, start_odometer_km, end_odometer_km, "
            "regen_kwh, duration_min, efficiency_kwh_100km) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso(dep), iso(arr), wps[0][0], wps[0][1], wps[-1][0], wps[-1][1], round(dist_km, 1),
             round(s0, 1), round(s1, 1), round(o0, 0), round(o1, 0), round(e * 0.18, 2),
             round(dur, 0), round(eff, 1)))
        tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i, (lat, lon) in enumerate(track):
            tt = dep + timedelta(minutes=dur * i / max(1, n - 1))
            sp = 0 if i in (0, n - 1) else round(avg_kmh * random.uniform(0.6, 1.4))
            sc = s0 - dsoc * i / max(1, n - 1)
            c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, "
                      "speed_kmh, soc) VALUES (?,?,?,?,?,?)", (tid, iso(tt), lat, lon, sp, round(sc, 1)))
            if i % 3 == 0:  # mirror a sparser path into positions (map/history)
                state["odo"] = o0 + dist_km * i / max(1, n - 1)
                state["soc"] = sc
                pos(tt, lat, lon, speed_kmh=sp, gear="D", is_locked=0, climate_on=int(climate),
                    climate_target_temp=20.0)
        state["soc"], state["odo"], state["t"] = s1, o1, arr
        return arr

    def charge(plug_t, loc, target_soc, kw, price_per_kwh, dc=False):
        s0 = state["soc"]
        if target_soc <= s0:
            return plug_t
        dsoc = target_soc - s0
        energy = dsoc / 100.0 * CAP                         # battery energy
        dc_energy = energy / 0.985                          # ∫V·I delivered (SoH ~98.5%)
        dur = (dc_energy / kw) * 60.0 * (1.25 if dc else 1.0)  # DC tapers a bit
        end_t = plug_t + timedelta(minutes=dur)
        lat, lon = (HOME if loc == "HOME" else (HPC if dc else WORK))
        ac_energy = round(energy / 0.9, 2) if loc == "HOME" else None  # wallbox AC (≈11% loss)
        billed = ac_energy if loc == "HOME" else round(energy, 2)
        cost = round(billed * price_per_kwh, 2)
        c.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc, "
            "energy_added_kwh, duration_min, latitude, longitude, charge_type, location_type, "
            "max_power_kw, cost, ac_energy_kwh) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso(plug_t), iso(end_t), round(s0, 1), round(target_soc, 1), round(energy, 2),
             round(dur, 0), lat, lon, "DC" if dc else "AC", loc, round(kw, 0), cost, ac_energy))
        steps = max(5, int(dur / 3))
        for i in range(steps + 1):
            tt = plug_t + timedelta(minutes=dur * i / steps)
            sc = s0 + dsoc * i / steps
            taper = 1.0 - 0.5 * max(0.0, (sc - 60) / 40) if dc else 1.0
            v = 402 if dc else 366
            a = (kw * 1000 / v) * taper
            state["soc"] = sc
            pos(tt, lat, lon, charging=1, plug_connected=1, is_locked=1,
                charge_voltage_v=round(v + random.uniform(-4, 4), 1),
                charge_current_a=round(a, 1),
                remaining_charge_min=int(dur * (1 - i / steps)),
                charge_completed=1 if sc >= 99.5 else 0)
        state["soc"], state["t"] = target_soc, end_t
        # settle: plugged, idle
        pos(end_t + timedelta(minutes=2), lat, lon, plug_connected=1,
            charge_completed=1 if target_soc >= 99.5 else 0)
        return end_t

    def park(dt, lat, lon, hours):
        """Overnight/idle samples (vampire drain reads these)."""
        steps = max(1, int(hours / 3))
        for i in range(1, steps + 1):
            tt = dt + timedelta(hours=3 * i)
            state["soc"] = max(2.0, state["soc"] - 0.04 * 3)   # ~0.3 %/day vampire
            pos(tt, lat, lon)
        state["t"] = dt + timedelta(hours=hours)

    # ── generate the month ───────────────────────────────────────────────────
    day = start
    sea_done = False
    for n in range(30):
        wd = day.weekday()
        morning = day.replace(hour=8, minute=random.randint(0, 30))
        # the one weekend at the sea (a Saturday in week 3)
        if wd == 5 and 12 <= n <= 20 and not sea_done:
            sea_done = True
            t = day.replace(hour=9, minute=10)
            if state["soc"] < 90:
                t = charge(day.replace(hour=1, minute=0), "HOME", 95, 7.4, 0.25)
                park(state["t"], *HOME, hours=6)
                t = day.replace(hour=9, minute=10)
            t = drive(t, SEA_WP, 121.0, EFF_HWY, 92, climate=True)   # Bologna → Rimini, cooled
            for minutes in (1, 2):                                   # parked, the cooling off
                pos(t + timedelta(minutes=minutes), *RIMINI)
            park(t, *RIMINI, hours=5)
            t = charge(state["t"], "HPC", 85, 110, 0.69, dc=True)   # the expensive HPC
            t = drive(t + timedelta(minutes=20), list(reversed(SEA_WP)), 121.0, EFF_HWY, 90)
            park(t, *HOME, hours=10)
            day = day + timedelta(days=1)
            continue
        if wd < 5:  # weekday commute
            precondition(morning, *HOME)
            t = drive(morning, COMMUTE_WP, 14.0, EFF_CITY, 34)
            park(t, *WORK, hours=8)
            t = drive(state["t"], list(reversed(COMMUTE_WP)), 14.0, EFF_CITY, 32)
            unload(t, *HOME)
            # occasional evening errand
            if random.random() < 0.3:
                t = drive(t + timedelta(minutes=40),
                          [HOME, (44.4750, 11.3650), HOME], 7.0, EFF_CITY, 28)
            # charge overnight when low, on the off-peak band (after 23:00)
            if state["soc"] < 45:
                plug_in(day.replace(hour=19, minute=30), *HOME)
                charge(day.replace(hour=23, minute=20), "HOME", 90, 7.4, 0.25)
                park(state["t"], *HOME, hours=6)
            else:
                park(t, *HOME, hours=12)
        else:       # weekend: mostly parked, maybe an errand
            if random.random() < 0.6:
                t = drive(day.replace(hour=10, minute=30),
                          [HOME, (44.4600, 11.2900), (44.4750, 11.3650), HOME], 12.0, EFF_CITY, 26)
                park(t, *HOME, hours=20)
            else:
                park(day.replace(hour=8), *HOME, hours=22)
            if state["soc"] < 50:
                charge(day.replace(hour=23, minute=30), "HOME", 90, 7.4, 0.25)
        day = day + timedelta(days=1)

    # ── final "now" snapshot: CHARGING at home — shows the hero charge animation
    #    + the Wallbox live panel out of the box ──────────────────────────────
    if state["soc"] < 50:
        charge(now - timedelta(hours=10), "HOME", 75, 7.4, 0.25)
    state["soc"] = 64.0
    for minutes in (6, 3):                 # two frames, so the cable counts as connected on Events
        pos(now - timedelta(minutes=minutes), *HOME, charging=1, plug_connected=1, is_locked=1,
            charge_voltage_v=362.0, charge_current_a=20.0, remaining_charge_min=68,
            windows_open=0, windows_open_count=0)

    c.commit()
    db.derive_events(1, max_rows=None)        # the demo runs the web alone, so the poller's job is done here
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("trips", "charges", "positions", "trip_positions", "events")}
    print("demo.db seeded at", DB_PATH, counts,
          "| last soc", round(state["soc"], 1), "| odo", round(state["odo"]))


if __name__ == "__main__":
    main()
