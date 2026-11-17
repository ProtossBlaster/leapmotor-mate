# Summer and winter tyres — design (D#493)

Approved by Silvio on 17/11/2026.

## Intent
D#493 (@DanielBrol): a driver who swaps winter and summer tyres wants the kilometres of each set
counted on their own. In scope (Silvio), opt-in.

## What it does
- **Settings → 🛞 Summer and winter tyres** (a card of its own: Settings has no Maintenance section).
  A switch, off by default. When on, two day/month fields the user chooses: *winter tyres on* and
  *summer tyres on*. Both are required and must differ; otherwise nothing is saved and the card says why.
- The setting belongs to the selected car (`_key` with the VIN), like the maintenance start of service.
- **Maintenance page**, only when the switch is on and both dates are set: a 🛞 card with the set on the
  car today (by the dates), the kilometres of the current season, and the total kilometres of each set
  over every season in the history, counted from the odometer. It says from which day the count starts.
- The dates repeat every year. Kilometres are counted backwards too, from the odometer already stored.

## Non-goals
- No change with the switch off, on any page.
- The official tyre items of the maintenance schedule are not split by set.
- No MQTT, no Home Assistant entity.

## Simplifications (declared)
- Days with no odometer reading are not estimated: the distance across a gap goes to the day the next
  reading arrives, and so to that day's set.
- 29 February cannot be chosen: a date must exist every year.
