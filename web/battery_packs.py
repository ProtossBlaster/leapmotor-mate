"""The battery packs Mate offers, per model — and nothing else.

A plain data table, deliberately in a module of its own rather than inside `main.py`: importing
main needs fastapi, which the minimal CI environment does not have, so the only file that asserted
these figures (`test_reev_variant_setup.py`) skipped itself there and the numbers travelled
unprotected. They decide every kWh, €/kWh and consumption figure an install will ever print.

T03: single EU variant → auto-set (no user selection needed).
C10/B10/B05/A10: two EU variants → selector shown.

🔑 A10 is the key, B03X is the name. The cloud reports the CHINESE project name, and every table
Mate keys on `car_type` sees only what the cloud sends. Labels say B03X, because that is the word on
the owner's papers. (Renamed because "A10" read as an A-segment while the car is a B — the same
convention by which the B10 is a C and the C10 a D.)

⚠️ **B03 and B03X are two different cars**, and only one of them is here:

    cloud says   sold in Europe as   body                          our row
    A10          B03X                crossover, 4270 mm, raised    yes → 39.0 / 52.0
    A05          B03                 hatchback,  4175 mm           no — read on

They are one character apart in writing and they are not the same car — the B03 is the A05, a
separate model announced beside the A10. They do share the hardware: the same platform, the same
2605 mm wheelbase, the same two motors and, per the press, the same two Gotion LFP packs (39.8 and
53.0 gross). The buffer belongs to the pack, so the usable figures would carry over.

🔴 **The B03 is deliberately absent anyway.** It is not on sale yet — Italy at the end of 2026, the
UK in 2027 — and its specification is not published: neither source that prints both battery columns
lists a B03 at all, it has no European WLTP figure, its trims are unannounced, and one report says
the smaller pack may not even reach Europe. A row written from the B03X's numbers would be a
capacity nobody has confirmed sitting in the field that decides every kWh, €/kWh and consumption
figure that install prints — which is #246 in another dress. The first A05 to connect takes the 65.0
unknown-model fallback, exactly as the A10 did, and the log names it ("Please report this model");
the fix is then one row, read the same way these were. Do not assume the A10 row covers it.

Per-variant USABLE (net) capacity, kWh — the energy between the BMS's protective limits, not the
gross pack. Sourced from EV Database / manufacturer sheets (cross-checked):

    T03   gross 37.3 → usable 36.0          C10 RWD gross 69.9 → usable 67.0
    B10 Pro     56.2 → 55.0                 C10 AWD gross 84.0 → usable 81.9
    B10 Pro Max 67.1 → 65.0 (2.1 kWh / 3.1% buffer, confirmed by 2 sources)
    B05 Pro     56.2 → 55.0   ·  B05 Pro Max 67.1 → 65.0 (shares the B10 pack; WLTP 401 / 482)
    B03X        39.8 → 39.0   ·  B03X        53.0 → 52.0 (LFP Gotion; WLTP 292 / 382)

⚠️ The C10 RWD was 69.9 until v3.11.1, taken from EV Database — the ONE source that reads 69.9 as
the usable figure and estimates a 72.0 gross on top of it, a number Leapmotor does not publish.
EVKX and EVspecs both read the 69.9 nameplate as the GROSS and give 67.0 usable (2.9 kWh / 4.1%
buffer). @ghuaywen-ai's own charges settled it (#246): on five sessions where he typed his
charger's own meter reading, ΔSoC × 69.9 made the battery take 100.8% and 100.0% of what the
charger delivered — more energy in than out, which does not exist. At 67.0 the same five land at
90.8–96.6%, with the AC session below the DC ones, which is the shape charging losses have.

These are the DEFAULTS for new setups; existing installs keep whatever they configured (no silent
migration of a calibrated value). NB on the B10 Pro Max: the car's DISPLAYED SoC 0–100% is
calibrated close to the GROSS 67.1 — a real-car ∫V·I measurement matched ΔSoC×67.1 within ~1% on
mid-SoC charges — so a B10 owner may see energy run ~3% low on the usable default; the Settings
"use measured" button (from the SoH estimator) lets them self-correct toward the value their own
car actually uses.

🔑 The wizard's manual-entry hint quotes these same figures, and a test holds the two together:
they used to disagree, mixing gross and net inside one line.
"""

EU_BATTERY_MAP: dict[str, list[dict]] = {
    "T03": [
        {"v": "36.0", "label": "36.0 kWh usable"},
    ],
    "C10": [
        {"v": "67.0", "label": "67.0 kWh usable — RWD"},
        {"v": "81.9", "label": "81.9 kWh usable — AWD"},
        {"v": "28.4", "label": "28.4 kWh — REEV (range-extender)", "reev": True},
    ],
    "B10": [
        {"v": "55.0", "label": "55.0 kWh usable — Pro · 361 km WLTP"},
        {"v": "65.0", "label": "65.0 kWh usable — Pro Max · 434 km WLTP"},
        {"v": "18.8", "label": "18.8 kWh — REEV (range-extender)", "reev": True},
    ],
    "B05": [
        {"v": "55.0", "label": "55.0 kWh usable — Pro · 401 km WLTP"},
        {"v": "65.0", "label": "65.0 kWh usable — Pro Max · 482 km WLTP"},
    ],
    # The cloud calls it A10; the car is a B03X. Both figures are the USABLE ones: their owner's
    # spec sheet quotes the gross 39.8 / 53.0, which is what the first B03X to reach us typed into
    # the manual field by hand (#338) — right car, wrong side of the ~2% buffer.
    "A10": [
        {"v": "39.0", "label": "39.0 kWh usable — B03X · 292 km WLTP"},
        {"v": "52.0", "label": "52.0 kWh usable — B03X · 382 km WLTP"},
    ],
}
