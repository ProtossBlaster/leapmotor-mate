# Charging places — candidate for discussion #288

Costs → Charging places configures private locations per selected vehicle: name,
coordinates (map picker or numeric input), 25–500 m radius, fixed price per kWh,
and an automatic-matching checkbox. Disabling a place keeps it available manually.
This does not switch a Home Assistant/wallbox profile or alter its global prices.

New live AC sessions match only when the frame is at most 120 seconds old, the
vehicle is stationary in P, the DC connector is explicitly absent, GPS is valid,
and exactly one enabled zone contains it. Missing/ambiguous evidence leaves the
place unassigned. Reconstructed/offline sessions are never matched from their
current position. GPS accuracy itself is not provided by every vehicle; owners
must choose suitably separated zones and may correct the assignment.

A charge saves its own place name and rate. Editing/disabling the place never
rewrites these snapshots. A closed, unmerged charge offers a place picker, including
removal of the place tariff. User-entered totals and free charges remain authoritative.
Different place/rate snapshots cannot be merged. For a merged session, unmerge before
changing its place. Spending by place appears on Costs for the selected vehicle.

This first version supports fixed private-place tariffs. It does not infer public
roaming/subscription prices or change global time-of-use/solar/dynamic profiles.
Without measured charger energy, the price uses battery energy and is an estimate;
it does not invent AC conversion losses.

## Validation and rollback

Synthetic database tests cover fresh/stale/absent/out-of-range GPS, future timestamps,
AC/DC/unknown connector, overlapping and disabled zones, per-car ownership and stale
forms, tariff snapshots, free/manual prices, reconstruction, closing, retagging,
merging, totals and repeatable schema expansion. Chromium exercises desktop/mobile
configuration; the existing map toggle regressions remain in the same candidate.

The schema adds one table and nullable charge columns. Existing rows are not backfilled.
Old Mate can still read the database, but does not understand place tariffs when editing
charges. For an exact rollback of a trial, stop Mate and restore the pre-test SQLite
backup together with the previous image; do not blindly replace a database containing
new real trips/charges. No automatic rollback or destructive schema contraction occurs.
