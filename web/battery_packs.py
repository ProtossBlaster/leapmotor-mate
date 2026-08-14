"""The battery packs Mate offers, per model — and nothing else.

A plain data table, deliberately in a module of its own rather than inside `main.py`: importing
main needs fastapi, which the minimal CI environment does not have, so the only file that asserted
these figures (`test_reev_variant_setup.py`) skipped itself there and the numbers travelled
unprotected. They decide every kWh, €/kWh and consumption figure an install will ever print.

T03: single EU variant → auto-set (no user selection needed).
C10/B10/B05: two EU variants → selector shown.

Per-variant USABLE (net) capacity, kWh — the energy between the BMS's protective limits, not the
gross pack. Sourced from EV Database / manufacturer sheets (cross-checked):

    T03   gross 37.3 → usable 36.0          C10 RWD gross 69.9 → usable 67.0
    B10 Pro     56.2 → 55.0                 C10 AWD gross 84.0 → usable 81.9
    B10 Pro Max 67.1 → 65.0 (2.1 kWh / 3.1% buffer, confirmed by 2 sources)
    B05 Pro     56.2 → 55.0   ·  B05 Pro Max 67.1 → 65.0 (shares the B10 pack; WLTP 401 / 482)

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
}
