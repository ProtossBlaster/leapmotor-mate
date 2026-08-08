"""Decisive test for the per-vehicle scoping.

Two identical instances of the SAME merged code over the SAME database, except one has a
second vehicle in it. If the scoping is complete, every page must render IDENTICALLY: the
second car is simply not the selected car, so nothing about it may reach the screen.

Any difference is either a leak (car 2's data escaping) or a scoping gap (car 2 changing an
aggregate). Volatile bits — clocks, cache-busting query strings, CSRF-ish nonces — are
normalised away first so they can't masquerade as findings.
"""
import difflib
import re
import sys
import urllib.request

A, B = "http://localhost:4002", "http://localhost:4003"      # 2 vehicles / 1 vehicle
PAGES = ["/", "/trips", "/map", "/charges", "/costs", "/statistics", "/report", "/battery",
         "/maintenance", "/commands", "/scheduling", "/vehicle", "/wallbox", "/navigation",
         "/prepare-car", "/settings", "/fuel"]
APIS = ["/api/overview-hero", "/api/status-card", "/api/battery-card", "/api/cumulative-summary",
        "/api/energy-breakdown", "/api/consumption-rank", "/api/charging-live", "/api/cmd-grid",
        "/api/energy-since-charge", "/api/diagnostics/missed-charges", "/api/v2l-card",
        "/api/charge-plan", "/api/charge-schedule", "/api/wallbox/summary"]

VOLATILE = [
    (re.compile(r"\?v=\d+"), "?v="),                     # cache busters
    (re.compile(r"\d{2}:\d{2}:\d{2}"), "HH:MM:SS"),      # wall clocks
    (re.compile(r'id="[a-z0-9]{8,}"'), 'id="X"'),        # generated ids
]


def norm(s):
    for rx, rep in VOLATILE:
        s = rx.sub(rep, s)
    return s


def fetch(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=40) as r:
            return r.status, norm(r.read().decode("utf-8", "replace"))
    except Exception as e:                                # noqa: BLE001
        return None, f"ERRORE {e}"


diffs = 0
for group, paths in (("PAGINE", PAGES), ("API/FRAMMENTI", APIS)):
    print(f"\n===== {group} =====")
    for p in paths:
        sa, a = fetch(A, p)
        sb, b = fetch(B, p)
        if sa != sb:
            diffs += 1
            print(f"  🔴 {p:<30} stato DIVERSO: 2 auto={sa} · 1 auto={sb}")
            continue
        if a == b:
            print(f"  ✅ {p:<30} identica ({sa})")
            continue
        diffs += 1
        d = [l for l in difflib.unified_diff(b.splitlines(), a.splitlines(),
                                             "1 auto", "2 auto", lineterm="", n=0)][:14]
        print(f"  🔴 {p:<30} DIVERSA ({sa}) — {len(a) - len(b):+d} caratteri")
        for line in d[2:]:
            print(f"       {line[:160]}")

print("\n" + "=" * 66)
print("✅ SCOPING COMPLETO: la seconda auto non cambia NULLA di ciò che si vede"
      if not diffs else f"🔴 {diffs} endpoint cambiano con la seconda auto nel database")
sys.exit(1 if diffs else 0)
