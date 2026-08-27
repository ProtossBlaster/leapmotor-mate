# Changelog

All notable changes to LeapMotor Mate are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [3.14.20] — 2026-08-27

**The Icelandic króna, and charging prices are no longer capped at 9.99** (discussion [#265](https://github.com/ProtossBlaster/leapmotor-mate/discussions/265), asked by **@alloutnow**).

Iceland — and Japan, Korea, Hungary — prices electricity in tens or hundreds of currency units per
kWh, but the charging-price fields refused anything above `9.99`, a ceiling that only ever suited
euro/dollar-scale tariffs. That cap is gone (the `0` floor and decimal entry stay), so an
ISK/JPY/KRW/HUF tariff goes in exactly as typed. The **Icelandic króna** (`kr.`) is now in the
currency list.

And every amount now shows **at least two decimals**, even for zero-minor-unit currencies (ISK, JPY,
KRW, HUF): collapsing to whole units would round the figure on screen, and Mate shows the number in
full.

## [3.14.19] — 2026-08-27

Two requests from the discussions, in one release.

**Outside temperature, from the weather — on the Overview, in Home Assistant, and on every trip** (discussion [#261](https://github.com/ProtossBlaster/leapmotor-mate/discussions/261), asked by **@wlighter**).

The Leapmotor cloud reports the cabin temperature but never the air outside — and neither does the
official app. Mate now fills that gap from Open-Meteo (keyless, free): while the car is awake it
samples the temperature at its current position — at most once every 20 minutes or 10 km, whichever
comes first — and shows it next to the cabin reading on the Overview, publishes it as an **Outside
Temp** Home Assistant sensor, and gives each trip its own start/end temperature from the readings
taken along the route (the post-trip weather lookup stays the fallback for trips recorded before the
feature was on).

Off by default: the lookup sends the car's position to Open-Meteo, so it is a single opt-in switch
under **Settings → trip defaults**. A car that has never reported a reading hides the row entirely,
exactly like the other optional temperatures ([#144](https://github.com/ProtossBlaster/leapmotor-mate/issues/144)).

**The database backup downloads compressed** (discussion [#264](https://github.com/ProtossBlaster/leapmotor-mate/discussions/264), asked by **@joeyoong**).

Settings → Export → the database backup now downloads gzip-compressed (`leapmotor_mate.db.gz`),
streamed in chunks so even a large database never lands in memory whole. Restore accepts **both** the
new compressed file and a raw `.db` backup taken before this change, so nothing you already saved
stops working — and a smaller file is easier to keep or sync wherever you back up. A Mate database is
mostly repetitive telemetry, which gzip shrinks well. Backing up straight to a cloud drive
(OneDrive/Google Drive), asked in the same thread, would need per-provider integrations Mate doesn't
carry; Home Assistant's own backup, a synced folder on the data volume, or rclone already cover that
— now on a smaller file.

## [3.14.17] — 2026-08-26

**The cost per 100 km is the sum of the trips — and a range-extender's electricity is priced the same on every page** (beta [#36](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/36) follow-up + discussion [#207](https://github.com/ProtossBlaster/leapmotor-mate/discussions/207), found by **@michapr**).

Two threads that turned out to be one. The Monthly Report priced its "cost per 100 km" from
charge_cost/km — the money charged over the kilometres driven — so it climbed when you charged with
the car parked, and it folded in energy charged but not yet driven. It now **sums the per-trip cost**,
the same figure the Trips and Statistics pages carry.

And that per-trip cost, on a range-extender, was itself split-brained: the trip **detail** priced the
electricity from the depleting **paid stock** (where generator kWh are free — already paid for in
litres), while the list, calendar, Statistics and the Report priced it through the blended €/kWh —
wrong for a REEV, which made the calendar disagree with the detail (on @michapr's data, **9.01 €
against 11.35 €** for one month). Every page now reads the REEV electric cost from that one paid-stock
replay, so a trip costs the same wherever you read it. **A pure electric car is untouched** — it keeps
the blended €/kWh, exactly as before.

## [3.14.16] — 2026-08-26

**The wallbox "Session energy" is the session, not the meter's whole life** (beta [#37](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/37), found by **@michapr**).

The live Wallbox card printed the energy sensor raw. For a cumulative counter — a `_total` sensor,
which is what Tasmota, Shelly and most smart plugs expose, and the very reading Mate deltas
start-to-stop for each charge — that is the meter's entire life: @michapr's tile read 162.58 kWh
under a label that says "Session energy". It now shows the reset-safe running figure the poller
already keeps on the charge in progress, and "—" when nothing is charging (the honest answer when
the car is asleep and no session was ever opened).

## [3.14.15] — 2026-08-26

**Always charging at home** (discussion [#255](https://github.com/ProtossBlaster/leapmotor-mate/discussions/255), asked by **@CartusGress**).

An install with no wallbox and no Home Assistant has no signal that a charge happened at home, so
every charge is born unclassified and the owner tags each one by hand — a lot of identical clicks for
someone who only ever charges at home, several short top-ups a day. A new opt-in under Settings ▸
Charge detection turns it around: while it is on, a new charge is born Home, editable afterwards for
the rare public one. It works forward only — the past backlog is left exactly as it was — and, so it
can never be switched on by accident, enabling it goes through an explicit confirm.

## [3.14.14] — 2026-08-26

**The monthly report's cost per 100 km counts the petrol too** (beta [#36](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/36), found by **@michapr**).

On a range-extender the Report's "cost per 100 km" tile answered with only the electric charge: a
month that burned a tank of petrol read 2.66 €/100 km where the real figure — electricity plus the
petrol actually burned — was near 10. The litres were already shown right above it; only the cost
side ignored them. It now adds the fuel at the same per-trip weighted-average price the Trips list
and the day totals already use (litres burned × the tank's blended €/L), so all three pages agree.
Verified on @michapr's August: **2.66 → 9.86 €/100 km**. Battery-only cars and months are untouched.

## [3.14.13] — 2026-08-26

**Consumption against outside temperature, one point per trip** (Statistics page — inspired by
[ghianciulu's MG4 Mate](https://github.com/ghianciulu/mg4-mate), a fork that went its own way).

The question every owner asks when the cold arrives — *how much does MY car really drink at 5 °C?* —
now has its own card on Statistics: one dot per finished trip against the outside air temperature
the elevation enrichment already collects (Open-Meteo), so it costs one query and zero cloud calls.
A dashed trend line reads your own winter and your own summer out of it; on realistic synthetic data
the pattern is legible after a single month of driving.

On a range-extender the chart carries **two series**: the electric points are battery-only by
construction (finalize blanks generator-on trips), and — behind the usual is_reev+research gate — a
petrol series plots L/100 km against °C, showing exactly when the generator starts costing more than
the socket. Mid-trip refuels never become points (those litres are unknowable); merged trips count
once; short hops under 3 km stay out, because preconditioning spread over three kilometres reads as
a cold penalty that isn't one.

## [3.14.12] — 2026-08-26

**Edit a refuel after the fact** (beta discussion [#34](https://github.com/ProtossBlaster/MateBetaTesterOnly/discussions/34), asked by **@pdifeo**).

The price is the one thing the cloud can never know about a refuel — and the one thing a pump
receipt corrects: a discount applied at the till, a total that quietly includes a coffee. Until
now the only way to fix a typed-wrong €/L was to delete the whole entry and retype it. Every refuel
row on the Rifornimenti page now carries a ✏️ that opens the add form's twin in place, pre-filled:
litres, €/L or total, note, and the instant itself. The server keeps the price pair consistent
exactly as the add form does — a filled €/L wins over the total, and editing the litres re-prices
the session against its €/L. The WAC's residual snapshot moves only when the instant moves, so a
price correction never rewrites the blend behind every trip's fuel cost. Annulla swaps the row back
without a reload; saving refreshes the list and the month calendar like every other fuel action.

## [3.14.11] — 2026-08-26

**One consumption figure, the same on every page** (beta [#35](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/35), found by **@michapr**).

The Trips, Statistics and Monthly Report pages each worked kWh/100km out their own way, so one month
could read three numbers. On @michapr's July: **10.1** on Trips (getEC over the km getEC covers),
**12.2** on Statistics (from `efficiency_kwh_100km`, over its own km, on UTC months, counting merged
children), and a third on the Report (getEC's *driving* share instead of the total). The Statistics
breakdown now runs on the same machinery the Trips calendar always has, and the Report's measured
average reads the total getEC — so all three land on the same basis: getEC over the km getEC covers,
local months, non-merged trips. Verified on his data: 10.1 everywhere.

## [3.14.10] — 2026-08-26

**The charge-ETA target comes back on upgraded instances.**

Since 3.14.8 the Overview chip reads the charge limit from a per-VIN setting the poll loop fills from
the car. On an instance that still carried a legacy shared `charge_limit_percent` (from an older
version, or the Set-limit button), the loop's "changed?" check read that shared value as a fallback,
saw it already matched the car, and so never wrote the per-VIN key — leaving the chip with no
"to X%" target. A fresh install wrote the key and hid it; an upgraded one did not. The check now
compares against the per-VIN value alone, so the key is written on the next poll and the target
returns.

## [3.14.9] — 2026-08-26

**BetaTester bundle: the refuels, and what each page computed** (beta [#35](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/35) and [#36](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/36), from **@michapr**).

Two range-extender questions arrived on the same day — a monthly cost that looked too low, and a
kWh/100km that read three different ways on three pages — and the diagnostic bundle could not answer
either: it carried the trips, but not the **refuels** behind the fuel cost, nor **what each page made
of the same trips**. Both are in it now. `fuel_purchases.csv` lists every refuel with the amounts,
the car it was logged against, and when it was entered; `page_stats.json` records the Trips,
Statistics and Report figures for each month side by side, plus the cost card's electric/fuel split —
so a number that disagrees across pages, and the inputs behind it, can be read from the pack instead
of reproduced by hand. No coordinates, no free text; the normal build is unchanged.

## [3.14.8] — 2026-08-25

**The charging dashboard shows the real target, not a fixed 100%** (beta [#33](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/33), found by **@pdifeo**).

While charging, the Overview chip read "· 7h 25m to 100%" even when the car was set to stop at 90%.
The time was right; the target beside it was not — it fell back to 100 whenever the car's scheduled
charging plan was switched off, which is the usual case. Mate had the real figure all along (the
Charges page shows it), so the dashboard now reads the same value — "to 90%" — and follows the limit
as you change it. On models that don't report a limit, the chip shows just the remaining time
instead of guessing.

**New sensor: Climate Power.** The car's climate power draw (signal 1348) is now published as a Home
Assistant sensor in watts, next to the other climate signals.

## [3.14.7] — 2026-08-22

**A merged charge is now priced on all of it** (#258, found by **@damde**).

Join two charge rows the car split apart, confirm the result, and Mate priced only the row you
clicked. The other pieces stayed untyped and unpriced — and the Charges page adds the pieces up, so
they counted as nothing. A 15 kWh home charge split 10 + 5 read **2,00 €** instead of 3,00 €.

The share that went missing is exactly the energy sitting in the other pieces, so the percentage is
the same wherever you charge — and the euros are worst where the tariff is highest:

| | should cost | showed |
|---|---|---|
| home, no wallbox | 3,00 € | 2,00 € |
| home, wallbox metered | 3,60 € | 2,40 € |
| AC charger | 6,75 € | 4,50 € |
| DC charger | 9,00 € | 6,00 € |
| HPC | 11,85 € | 7,90 € |
| price typed by hand | 9,90 € | 9,90 € — never affected |

Every piece is now priced the same way an unpaired charge is: on its own wallbox reading, or on its
own energy. Two figures belong to the whole session rather than to a piece — a price you typed by
hand, and a charger's own kWh reading (#222) — and those stay where you entered them, with the other
pieces carrying no cost, so the same energy is never billed twice. **The number you typed is never
rewritten or divided**: split the charge apart again and it comes back exactly as it was.

**Charges you confirmed before this release are put right on their own**, once, when Mate starts —
at the rate that group was priced at, not at today's prices. Without that the fix would have reached
only people who had not yet hit it. Nothing you had already confirmed is recalculated.

The 🆓 free mark had the same shape and was corrected with it: it wrote only the row you clicked, so
on a merged charge it would have left the group costing the other pieces' share instead of nothing.

Thanks to **@damde**, who found this, measured it and wrote the tests — nobody had reported it.

## [3.14.6] — 2026-08-20

**A signal the car never sends is not "Inactive"** (#256, @ghuaywen-ai).

> *"Security Status for C10 always shows Inactive, B10 does show Active. Is it a limitation in
> the C10?"*

It is the C10, and it is measured on three cars — signal **1255** (`vehicleSecurityActive`):

| car | signals it sends | 1255 |
|---|---|---|
| @ghuaywen-ai's C10 | 77 | absent |
| @ebagnoli's C10, over 17 continuous days | 88 | absent |
| @michapr's B10 | 98 | present, 252 samples, values 0/1/2 |

All three send 1256/1257/1258 (the ignition key positions). Only 1255 is missing.

The defect was ours. `int(sig.get("1255") or 0) != 0` turned an absent signal into a zero, and a
zero into **"Inactive"** — so Mate was not saying *"I don't have this figure"*, it was saying *"your
car is not protected"*. That is the one direction a missing value must never fall in, all the more
on a security row. The row is now simply not drawn when the car never said. A B10 loses nothing:
Active and Inactive both still render.

🔑 Four places, because this field had **two writers**: the poller parses it, the poller stores it,
**the web writes the same column on its own** through `save_fresh_signals` — fixing only the poller
would have brought the defect back on the first manual refresh — and the page draws it.

## [3.14.5] — 2026-08-18

**Two cars, two more places that were not asking which one** (#253, @cookingeek) — and a label on a
REEV figure that was being read as something it is not (beta [#31](https://github.com/ProtossBlaster/MateBetaTesterOnly/discussions/31), @gm27271).

### The third sweep for this defect family, and the first done in full

Rather than waiting for the next report, every screen was checked: 23 pages and 45 self-loading
panels, each fetched twice on a rig holding two cars with disjoint data. Two leaks, neither of them
ever reported by anybody.

- **The consumption cache did not know which car it held.** One cache serves four endpoints —
  energy breakdown, getPlugIn consumption, the period picker, since-last-charge — through nine key
  shapes, and not one carried the car (`"plugin6w"` was a literal constant). Look at one car's
  Statistics, switch inside the thirty-minute TTL, and its kilowatt-hours were served under the
  other car's name. 🔑 v3.14.3 is what made this bite: before it every cloud read answered for the
  first car on the account, so a shared cache was wrong but uniform.
- **Maintenance started from the wrong car.** The delivery date and odometer were install-wide, and
  the fallback was worse — the earliest date and lowest odometer of *every* car at once. Every
  service interval on that page counts from those. The services already done were per car; the
  start of service was not.

### The REEV electric figure says what it is

On an engine-on trip the list printed `⚡ 4.5 kWh/100 km` and nothing else. That is the energy which
left the **pack**, over all the kilometres: what the generator sends straight to the motor never
passes through the battery, so a long generator trip reads impossibly well. The trip detail has
explained this since v3.2.0; the list, where the number is met first, did not. It now carries a
short **battery only** label, with the full sentence in the tooltip.

Single-car installs see no change from this release.

## [3.14.4] — 2026-08-17

**And the command is now SHAPED for the car you picked, not just addressed to it** (#253,
@cookingeek).

v3.14.3, three hours earlier, fixed *where* a command goes. It did not fix *what is inside it*.
Three commands change their payload by model, and all three still read the model from the car the
account listed first:

- **windows** — the LEAP cars (B10/C10/B05) take 0–10, the T03 takes 0–100. On a T03-first account,
  "windows to 50%" reached the C10 carrying the T03's `50`, which is off the C10's scale entirely;
- **climate** — every write was rewritten to the T03's manual form (#67);
- **A/C off** — the T03's full seven-field body went to cars that need `ac_switch operate=off`.

The mirror case is the worse one: a **T03 second on a C10-first account never switched its A/C off
from Home Assistant**, because it got the one form #67 proved the T03 ignores.

The poller had already learned this — `_mqtt_windows_native` carries the scar in its own docstring
— and had applied it to the windows alone, leaving A/C-off eight lines below untouched. Both now go
through one `_mqtt_car_type(client, vin)`.

🔑 Why yesterday's guard missed it: it forbids `self._vehicle`, and this was spelled
`getattr(_session, "_vehicle", …)` in a module-level function. The guard is now on the attribute,
however it is written. Only accounts with **two cars of different models** were affected.

## [3.14.3] — 2026-08-17

**With two cars, a command reaches the car you picked** (#253, @cookingeek).

The session that talks to the cloud bound one vehicle when it logged in — the first the account
lists — and the picker never touched it. Every read on the page was scoped to the selected car;
every call to the cloud went to that first one. Lock and unlock, trunk, windows, climate, charge
start and stop, unlock-charger — carrying that car's PIN. Pick the second car, press Unlock, and the
first one opens.

Seventeen call sites took their car from the login. Yesterday's v3.14.1 fixed three of them — the
picture cache, the compose memo, the image token — and left the source, which is why the wrong car's
picture came straight back. The frozen car also reached the fresh-signal dump behind Diagnostics and
the bundle, the charge / climate / prepare-car schedules, and the whole consumption family (energy
breakdown, weekly rank, cumulative summary, and the four raw probes in the BetaTester bundle) —
which is almost certainly the "live stats not switching" half of the report.

The car is now resolved on every use, never frozen: the picker moves under a live session. With one
car, or with a selection naming a car the account no longer has, nothing changes.

## [3.14.2] — 2026-08-16

**Six reports from four owners, and one of them had been waiting three weeks.**

### Trips and charges that were joined
- **A run of joinable trips is drawn once** (#249, @riri19). The view proposed PAIRS, and pairs
  overlap: a trip between two others was the second of one and the first of the next, so it was
  drawn twice. On his own fortnight at the 90-minute stop he had chosen — 23 pairs, 46 cards for 34
  trips. A chain is now one block with a connector between each neighbouring pair; the merge itself
  is unchanged and still joins exactly two.
- **A stop inside a joined trip is marked on its chart** (#159, @pdifeo), shaded and labelled with
  its length. That blank stretch was indistinguishable from the car losing the cloud — and he was
  twice told it was exactly that.
- **The note of a joined charge describes the whole session** (#247, @Ng-EY), not its first piece.
  The twin of the trip-note fix in v3.11.2, reported by the same person on the same issue.

### What the charge figures are called
- **The charge ETA no longer quotes a switched-off plan** (#252, @ghuaywen-ai). What Mate showed as
  "the charge limit" is the target of the car's charging PLAN, and it was being used even with the
  plan off: his car read "to 90%" while charging to 100%.
- **That number is now named for what it is** — *scheduled charge target* — on the Charges page and
  on the battery bar too, in all eight languages. The upper limit in the car's own app is not
  something the cloud reports.

### Settings that quietly switch things off
- **Settings says when the charge-detection floor is above what the car actually draws** (#250,
  @riri19). His sat at 13.5 A on a car charging at 12.9–13.4: over two days Mate read "charging" in
  seven frames, and 10.2 kWh by his own wallbox counter went unrecorded. A floor that high does not
  produce "no charges" — it produces half of one.

### For range-extenders
- **Refuelling in the middle of a drive no longer erases that drive's petrol** (beta [#30](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/30), @pdifeo).
  Litres = start − end goes negative on a refuel, and the trip came out as fully electric. It is now
  unknown rather than zero, and says so.
- **The BetaTester bundle carries the charges and the behaviour settings.** It had neither, so a
  report about a charge — the first thing his note describes — could not be answered from it.

### Elsewhere
- **The diagnostics bundle downloads from a phone** (#252). It was a page navigation, which a Home
  Assistant webview drops in silence.

## [3.14.1] — 2026-08-16

**Three things the first install with two real cars found on day one** (#253, @cookingeek).

- **The "never set up" strip can be turned off by fixing the car.** Choosing that car's battery
  pack from Settings answers it, and so does giving that car its own PIN — which is the only way
  out for a car whose model default happens to be right and has nothing to change in the capacity
  box. Until now the mark was written in exactly one place in all of Mate, the setup wizard, so a
  car corrected properly went on being accused. Both stamp ONE car: an install-wide PIN answers for
  nobody, or the strip would vanish for a car nobody has looked at.
- **A phone can switch car again.** The picker was not hidden below 768 px — it was never drawn:
  it lives in the sidebar's desktop-only block, so a two-car install was stuck on whichever car was
  selected. It is now also in the ☰ drawer, under the heading. With one car nothing appears
  anywhere, exactly as before.
- **The car's picture follows the car you picked**, in the three places that each showed the wrong
  one on their own: the cached layer package (one file for the whole install), the composed-image
  memo (keyed on the body state, which two parked cars share) and the image URL's token (same
  state, so the browser kept the previous car for the five minutes it caches). Single-car installs
  keep their existing file and re-download nothing.

Also: the user manual now says **how Mate updates** — from the add-on, from Docker and from
MateDesktop — and explains the ↑ badge beside the version, in all five languages.

## [3.14.0] — 2026-08-14

**A car nobody was ever asked about now says so, and three figures stop counting the hours Mate was
not watching.**

### A car that walked in on its own
- **A strip across every page** when a car reached Mate without going through the wizard — what
  happens to a **second car** added to an install where the sign-in was already done. Until someone
  answers for it, that car runs on the **default battery pack of its model**: on a C10 that default
  is the RWD, so an AWD is 20% off and a REEV 2.4 times off, and its kWh, price per kWh and
  consumption all follow the wrong number in silence. The strip names the car and opens the wizard,
  where a pack and a PIN are chosen.
- **Nobody already installed is accused.** No install older than v3.13.0 carries the wizard's mark
  on any car, so "no mark" cannot mean "unconfigured": the poller marks, once, the cars present at
  the moment of the update, and only a car that arrives afterwards is uncovered. Between the update
  and the poller's first start the page says nothing at all rather than accusing every car.

### The Wallbox card (#248, @Ng-EY)
- **The warning names your own car.** It said *"B10 not connected"* to everyone — the model was
  written inside the sentence, in all eight languages, from when Mate only covered the B10. It now
  comes from the car itself, whatever model that is.
- **"Session cost" is now "Last home charge".** That tile never showed the session: a charge is
  priced only once it ends, so even while you are plugged in and charging it prints the previous
  one. In Mate "home" means wallbox **or** domestic socket, so the label promises no wallbox either.

### Figures that were counting blackouts
- **Average charge duration** and **drive time** leave out the reconstructed rows. Those are jumps
  found after the fact, across hours when the car or Mate was offline: their "duration" is the
  length of that silence. Measured on a real database, one such charge moved the average by +18%.
  Their kilometres and their energy still count — it is only the clock that is unusable — and each
  card says how many rows it left out.

### The battery figures
- **A C10 still on 69.9 kWh is told.** v3.11.2 corrected the RWD default to 67.0 usable after real
  charges settled it (#246), but only for new setups; every C10 already installed kept the nameplate
  figure and has been ~4% high ever since. Settings now says so beside the field and offers the
  corrected value with one button. It is never applied for you: a capacity is yours to calibrate.
- **The wizard's manual-entry hint quotes the packs Mate actually uses.** It mixed gross and net
  figures on one line, two languages still carried the disproved 69.9, and the B05 was missing.
  The field is no longer pre-filled with someone else's battery.

### Under the hood
- The test suite no longer writes a database into whatever directory it is run from.

## [3.13.1] — 2026-08-13

**Updating blanked the car-responsiveness badge — the commands it already had on record are given back.**

### Fixed

- 📶 **The car-responsiveness badge no longer goes dark on update.** 3.13.0 began recording which car
  each remote command was sent to, in a column that did not exist before: every command already on
  record was left without a car, and the badge counts only the rows that carry one. A working
  install lost up to 90 days of history in a single update and printed a dash until three fresh
  commands rebuilt it. Where the car is knowable those rows are handed back — with a single car on
  the account every command ever sent went to it, so it takes them and the badge crosses the update
  with its history intact. With two or more cars they belong to nobody and stay out: attributing
  them to the oldest car, or the selected one, or any car at all, would judge one car's coverage on
  another's diary, which is the defect 3.13.0 closed. The recovery reads the state of the database
  rather than the moment the column appeared, so an install that already took 3.13.0 is repaired as
  well, not only one arriving from 3.12.0.

## [3.13.0] — 2026-08-13

**A second car is now set up, not guessed at — and the settings that were shared between cars are not.**

### Added

- 🚗 **The setup asks about every car on the account.** It used to keep the first one the cloud
  listed and drop the rest, so on a two-car account you configured one and the other was registered
  later by the poller with its MODEL's default pack. For a C10 that default is the RWD: an AWD ran
  20% out and a range-extender 2.4 times out, silently, and nobody was ever asked. The pack cannot
  be derived — the cloud says only "C10" — which is why the wizard shows a list, and now shows one
  per car, each with its own PIN.

### Fixed

- 🔌 **The install-wide ABRP token stops covering every car** the day a second one arrives. Both
  cars answering to one token pushed two cars' positions and SoCs into a single ABRP vehicle. The
  token now moves onto the car that was here first and the newcomer sends nothing until it has its
  own — and Settings names the cars that would send nothing, instead of a status dot reporting
  "active" over silence.
- 🌡 **The ready-automation belongs to a car.** One shared config meant the temperature chosen for
  one car answered for both, and switching it off on one switched it off everywhere. The poller
  reads it by the VIN it is polling, not by the car selected in the web.
- 📶 **The command-responsiveness badge is per car.** `command_log` had no column for the car at
  all, so the fact was never recorded: the timeouts of one car pulled down the score of the other.
  Rows written before are left out rather than attributed to somebody.
- 🪟 **An MQTT window command is scaled by the car it is for.** It read the model off the first car
  on the account while "fully open" is 10 on a B10 and 100 on a T03 — sent to the T03, it opened
  them a tenth of the way.

## [3.12.0] — 2026-08-13

**A charge the car reported in pieces can be joined back into one, and split again.**

### Added

- 🔗 **Joining two charges.** The car declares the cable GONE the instant the current stops, on
  fresh frames, so a single plug-in can be recorded as several charges: @michapr (beta [#29](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/29)) was
  split by one 30-second frame, and a load-balancing wallbox turned one night into six rows. A
  grace window in the poller would have to guess how long a real pause lasts, and a closed charge
  is never recomputed — the guess would be silent and permanent. So the rows stay as the car
  reported them and you join them yourself, on the Charges page, the way trips are already joined.
  Joining writes one marker and nothing else, so splitting brings the original rows back exactly.
- The join is offered only where a close-enough charge sits before this one, and refused when the
  two belong to different cars, when either is still running, when another charge sits between
  them, or when the car drove in the gap — a trip overlapping it, or a SoC that fell.

### Changed

- **A joined charge counts as one everywhere, not just on the page.** The session tally, the
  €/kWh denominator, the averages, the confirm banner, the AC/DC split, the wallbox calendar, a
  trip's charge markers and the kilometres-since-the-previous-charge all read the whole session
  now. The state of health matters most: a split session handed it a fraction of the energy over a
  fraction of the SoC delta.
- Totals do not move. Energy, money and peak power are the same before and after a join — only
  the counts change.

## [3.11.2] — 2026-08-12

**The C10's battery figure was the gross one, and a merged trip's summary stopped at the midpoint.**

### Fixed

- 🔋 **The C10 RWD pack is 67.0 kWh usable, not 69.9.** @ghuaywen-ai (#246) reported that the
  charging-efficiency line had disappeared from a charge where he had typed in what his charger's own
  meter said it delivered. Nothing had broken: Mate withholds that line when the losses come out
  negative, and on that charge they did — **52.41 kWh delivered against 52.84 kWh "into the
  battery"**, which would have printed an efficiency of **100.8 %**. More energy into the pack than
  out of the charger does not exist, so the number that had to be wrong was ours.

  **The manufacturer declares one figure and never says which it is.** The Stellantis press kit's own
  specification table reads *"Battery (kWh) 69,9"* — no *total*, no *usable*. EV Database reads that
  69.9 as the **usable** capacity and estimates a 72.0 gross on top of it, a number Leapmotor does
  not publish; EVKX and EVspecs read the 69.9 nameplate as the **gross** and give **67.0 usable**
  (2.9 kWh buffer, 4.1 %). Mate had followed the first reading.

  **His five metered charges settle it.** At 69.9 two of them have the battery taking 100.8 % and
  100.0 % of what the charger delivered; at 67.0 all five land between **89.2 % and 94.9 %**, with
  the AC session below the DC ones — which is the shape charging losses actually have.

  New setups get 67.0. **An existing install keeps whatever it has**: a configured capacity may have
  been calibrated against the owner's own car and is never migrated silently. A C10 RWD owner who
  wants the correction sets it in *Settings*, and charges already recorded keep the energy they were
  written with — the figure is stored per charge, not recomputed.

  ⚠️ The C10 **AWD** entry (81.9 usable, 84.0 gross) rests on exactly the same assumption and is
  **left alone**: 81.9 is the manufacturer's unqualified number too, but no owner data exists to
  check it against, and inference is what produced the wrong figure in the first place.

- 🧭 **A merged trip's summary now describes the whole journey.** @Ng-EY (#247): merge A→B with
  B→C, press *Generate summary*, and the note came back describing **A→B**. The page had already
  resolved the merge — the map, the arrival time and the distance all reach C, because every reader
  composes the group on the fly (`_trip_group_stats`) while the stored rows stay untouched so that
  unmerging restores the originals exactly. The summary was the **one reader that did not**: it took
  the parent segment's own row, so the end address, the arrival time and the end temperature all came
  from the midpoint — the trip's own note contradicting the trip it is attached to, on the same
  screen. Pressing it on a merged **child** had the mirror problem: the note described that segment
  alone while the page showed the group. Both now resolve to the group, and the note is written to
  the parent — the row the page reads it back from.

### Internal

- 🧪 **Five tests for the merged summary**, each seen failing against the previous code before the
  fix went in, plus the four battery-variant assertions updated to 67.0. ⚠️ Worth knowing: that
  battery-variant module **skips itself in the CI environment** (it imports `web.main`, which needs
  fastapi, absent from the minimal CI install) — so the pack figures were guarded locally and by
  nothing at all in CI.

## [3.11.1] — 2026-08-11

**The range-extender's distance was counted on the coarse signal, and lost 42 % of it.**

### Fixed

- ⛽ **How far the generator drove, measured against the car's own dashboard.** @pdifeo (beta [#28](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/28))
  sent five drives with the thing we almost never get: **photographs of his C10's own screen**,
  which states the petrol distance per drive. Mate stated much less — 35.0 km where the car said
  60.2, and one 3 km drive where it said the generator had run and then showed no distance at all.

  **The litres were never wrong.** 3.591 L against 3.591 L on the car's own counter, and over the
  long window — since his 03/08 refuel — 14.248 L against the 14.37 his dashboard works out from
  "239.5 km · 6 L/100 km". Eight tenths of a percent apart. Only the DISTANCE was short.

  🔑 **The cause was a unit.** The walk asks, row by row, "did the odometer rise AND the fuel fall
  in this SAME row?" — and it asked the tank in **percent**, which moves in steps of 0.1: about
  47.5 mL, some 600 m of generator driving. Every row where the car had moved but the coarse gauge
  had not yet ticked was thrown away. The car reports the same tank in **millilitres** as well, and
  those were **already in the same database row** (`positions.fuel_liters`, written since v2.14.1).
  Reading them takes the same five drives from 35.0 km to **54.0 km** against the car's 60.2.

  ⚠️ Second time this file has made this mistake: `_reev_trip_fuel` had its noise floor on the
  percentage while reading the millilitres (beta [#22](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/22)/[#23](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/23)). Same disease, one function up.

  A car that does not send the millilitres, and every trip recorded before v2.14.1, keep the
  percentage walk — worse, but it is what those rows have, and it must not become nothing.

### Internal

- 🧪 **Two dead ends recorded in the code, so nobody walks them twice.** There is **no "generator
  running" signal** in the cloud: 1277 looks exactly like one — 0 across a pure-electric drive, 1
  across a petrol one — and across all 13 days of the bundle it fires in one-minute bursts at the
  start and end of drives, with 0.001 L burned inside them against 24.9 L outside. And an
  "anchor on the last fuel change" rule scores −2 % if you rebuild the trail from the raw signal
  log and **+54 %** on the real one, because it hands the generator the whole electric middle of a
  drive: the raw log records a signal only when it CHANGES, so rebuilding from it gives a timeline
  3× sparser than the poll grid the code actually walks. A rule tuned on that is tuned on an
  artefact.

## [3.11.0] — 2026-08-11

**Mate speaks Spanish.** Eighth language, complete: 1 178 strings, the setup wizard, the sign-in
page, the maintenance schedule and the calendar.

### Added

- 🇪🇸 **Español.** Asked for on Facebook by **Francisco Ruiz**, a Spanish owner running Mate today.
  Every surface, not just the locale file: `es.json` (1 178 keys), the setup wizard's own separate
  dictionary inside `setup.html`, and the four dictionaries in `maintenance.py` — 62 service items,
  categories and page chrome, plus the phrase fragments that build *«Primera revisión: dentro de
  11 meses»*. The language selector, the wizard's flag buttons, the browser-language detection and
  both allow-lists in `main.py` know about it.

  **European Spanish (es-ES), terminology checked rather than invented.** *Autonomía*, *maletero*,
  *cuentakilómetros*, *estado de carga*, *estado de salud*, *frenada regenerativa*, *punto de
  recarga*, *pastillas de freno*, *latiguillos*, *desempañado*. Vampire drain is **descarga
  pasiva**, the term the field actually uses — not a literal *vampiro*. The Mexican manual portal
  was deliberately not used: it would have given *odómetro*, *cajuela*, *llanta*.

- 📖 **A written manual in Spanish** — [`docs/MANUAL-DE-USUARIO-ES.md`](docs/MANUAL-DE-USUARIO-ES.md),
  the fifth alongside English, Italian, French and German: the whole of it, from the requirements and
  the wizard through every page, the integrations, the troubleshooting and the glossary. All four
  existing manuals, the README (both halves), FEATURES and the Docker Hub page now say **eight
  languages** and list the fifth manual. The manuals' own version lines had drifted apart — one
  header said v3.8.8 while its own footer said v1.27.0 — and all five now agree on the release they
  describe.

### Fixed

- 🏷️ **Five page titles, the mobile menu and the READY row were hard-coded English — in every
  language.** Found by reading the SERVED pages of a Spanish install instead of the templates:
  Trips, Charges, Statistics, Commands and Settings printed their English title in the browser tab
  for all seven existing languages, and had done since those pages existed. Nothing failed —
  a hard-coded string is not a missing key, so no completeness check could ever see it. Now they
  read from the same `nav_*` keys the other pages already used, and `menu_label` /
  `ready_state_on` / `ready_state_off` are translated in all eight languages.

### Internal

- 🧪 **The completeness test now counts the surfaces it was not counting.** It already guarded the
  locale files and the wizard; it now also guards `maintenance.py` (a third copy that falls back to
  English key by key, so a gap there shows a page half in your language) and refuses any
  `{% block title %}` with a hard-coded English word. Adding a language turns them red until every
  copy carries it, which is the point.

## [3.10.7] — 2026-08-11

**A trip abandoned by the cloud now ends when the car last spoke, not half an hour later.**

### Fixed

- 🕐 **The other end of the same silence.** v3.10.6 stopped a trip from *absorbing* kilometres nobody
  watched. This is its closing half: a trip left open on a frozen "gear D" frame is shut by the
  30-minute guard (#233), and it was stamped with the moment the guard fired — so it carried up to
  half an hour in which, by definition, nothing was observed. The duration grew, the average speed
  fell, and the car had been parked in the drive the whole time.

  The distance was never wrong: the frozen frame **is** the last real measurement, so the odometer
  was already right. Only the time lied.

  **The pattern already existed, one table over.** Since #208 a charge the car drove away from is
  closed on the last reading taken *while charging*, dated with the car's own clock (`frame_ts`)
  rather than with the poll that noticed — on @mikeeeeekoo's overnight charge, the difference
  between 06:10 and 09:36. Trips had no equivalent. They do now.

  🔑 Nothing new had to be recorded to know this: while DRIVING the recorder already skips saving a
  position for a repeated frame (#128), so the last stored position of such a trip **is** the last
  thing the car said. It was on disk the whole time.

  ⚠️ The car's clock is preferred but checked, exactly as the charge checks it: a frame timestamp
  from before the trip opened — host skew, or a partial frame carrying someone else's clock — would
  end the trip before it began, so it is only taken when it lands inside the trip.

  ⚠️ And the two halves of the ending now move together. `ended_at` came from one clock and
  `duration_min` from another; they agree in production and diverge the instant the end is anything
  other than now. It is now one moment, used for both.

  On a healthy link nothing moves: the last thing the car said is also this instant.

## [3.10.6] — 2026-08-11

**Kilometres driven while the car was out of contact now belong to no trip — they are measured, declared, and left out of every figure.**

### Changed

- 🚗 **A trip no longer absorbs the stretch nobody watched.** Traced end to end on **#244**
  (**@adoewa**): his car went quiet at 16:15, he drove 74 km home, parked overnight, and drove 2 km
  to the supermarket the next morning — and those 74 km were welded onto the front of the
  supermarket run. Because a trip's start time is the moment the link returned, they were also
  counted on the wrong **day**: missing from the 9th, added to the 10th.

  Put the car in D after a silent stretch and the trip was born carrying 50 km, the charge that went
  with them and a start point 50 km away, before the car had moved a metre.

  Those kilometres may belong to the drive that is starting, to the drive that ended hours ago, or
  to two drives with a night's parking in between — and **nothing in the data says which**. Three
  discriminants were measured against real logs and all three failed: **time** (a correct 4 km
  recovery after 383 minutes of silence), **absolute distance** (a legitimate 22 km anchor), and
  **the gear before the silence** (parked in the good case and the bad one alike).

  So nothing is guessed. The trip starts where the signal returned, and the unattributed stretch is
  recorded on its own.

### Added

- 📋 **Two places now say what was left out.** On **Statistics**, the running total; above the
  **Viaggi** calendar, the month you are looking at — inside the calendar itself, so it follows the
  month buttons instead of standing still while the grid moves. Each states the kilometres, the
  charge, the energy and, when Mate has an average price, the cost:

  > *measured, but not attributable to a specific trip — therefore excluded from distances,
  > consumption and costs*

  ⚠️ The charge always travels **with** the kilometres. Holding out one without the other would
  divide whole energy by partial distance — the shape of the SoH defect (v3.10.2) and of #237.

  ⚠️ The euro is omitted, not shown as zero, when no charge carries a price: Mate's average divides
  over priced charges alone, and a 0.00 there would invent a free kilometre.

  ⚠️ **Mate's total will now sit below the car's odometer**, permanently, by exactly that figure.
  That is the point: the difference is stated rather than quietly folded into a trip.

### Removed

- ⛔ **The start anchor (#130, v3.8.x, and #233, v3.8.8) is gone**, and both were built for
  **@riri19**. They moved a late-opening trip's distance, energy and start position back over the
  unseen kilometres, so his 19 km drive was drawn as leaving from home rather than 5 km down the
  road. That is exactly the guess above, and it cannot be told apart from @adoewa's case. His
  kilometres are not lost — they are on the new card — but his trips start where the signal returned
  again. Silvio's call, taken with the measurements in hand.

  🔑 It also takes the sting out of an arbitrary constant: the 30-minute frozen-drive limit still
  closes an abandoned trip, but it no longer decides which trip a day's kilometres land on.

## [3.10.5] — 2026-08-10

**A drive recovered after a blackout was stamped at the moment the cloud caught up, and a chart that had nothing to draw looked broken.**

### Fixed

- 🕐 **A trip rebuilt from an odometer jump now starts when the car last spoke, not one poll ago.**
  Measured on **@riri19**'s own database while triaging **#244**:

      2026-08-09T21:37 → 21:37   4.0 km   SoC 70.2→69.9   —min   reconstructed

  Start and end on the same instant and no duration at all. The kilometres were right — the
  odometer really did move — but the trip landed at the moment the link returned rather than when
  the car drove, which is why these read as "random trip entries" (**@wlighter**, #242) instead of
  the recovered drives they are.

  The baseline the reconstruction started from was re-stamped on **every poll**. Behind a frozen
  frame the car still looks online: Mate asks every 30 s, gets the same odometer back, and moves the
  baseline forward each time — so when the fresh frame finally arrives the whole drive is squeezed
  into the last polling interval. 4 km in 30 s is 480 km/h, and the plausibility guard threw the
  duration away. The guard was right; what reached it was wrong.

  Mate already knows a repeated frame when it sees one. A second baseline now moves only on a
  **fresh** frame, and the gap spans the real blackout — so the guard can finally do its job:
  40 km across a 45-minute dark window keeps its 45 minutes, while 4 km discovered after four hours
  of silence still refuses to guess. Simulated in a container against the released code, same
  frames: `18:45:00 → 18:45:30, duration discarded` became `18:00:00 → 18:45:30, 45.5 min`.

  ⚠️ A car whose frames carry no clock cannot be judged this way and is untouched.

- 🦇 **The standby-drain chart now says why it has no new bar.** **@riri19** again (#241):
  *"the data seems stuck since 05/08"*. It was not stuck. Every stop since had lost **0.1%** — one
  step of the car's charge sensor — and a drop that small cannot be told apart from noise, so
  nothing was drawn. His bundle answered it in a minute; his screen could not, because the page said
  nothing at all about the stops it discarded.

  The rejected stops, with their reason, have been in the data since v3.10.2 — the page received
  them and printed none. It now names the most recent one under the chart: how long it lasted, what
  it lost, and why it was not charted. In all seven languages, with each language's own decimal mark.

- 📏 **The noise floor was labelled "%/day" in seven languages and in the diagnostics bundle.** It is
  not a rate: it is compared against the raw drop in SoC points, so a 12-hour stop and a three-day
  one face the same 0.2. The wrong unit invites exactly the wrong fix — raising the threshold to
  catch a slow drain. The Settings label was always right; the battery page and the bundle were not.

## [3.10.4] — 2026-08-09

**The charge cable went missing whenever the charge was waiting for its programmed window — and it took the window with it.**

### Fixed

- 🔌 **The Overview drew no cable while the car was plugged in and waiting.** Reported by
  **@rop12770** (#243, C10): the official Leapmotor app showed the car plugged into a wallbox, Mate
  showed it standing alone.

  The cause is one number. Mate reads the cable from signal **1149**, and accepted **1, 2 and 3** as
  connected while rejecting 0 and 5. The car also emits **4** — cable in, charge deliberately
  postponed to its programmed window — and 4 was in neither list, so it fell into "unplugged" by
  exclusion.

  Measured on a second car the same evening: enabling the schedule mid-charge stopped the charge and
  switched the code from 2 to **4**, cable untouched. The frame the cloud then repeated for three and
  a half hours carried `47 = 1` (AC input present), `1178 = 0.1 A` and a schedule of `01:50–12:00` —
  and the official app, reading **that same frame**, drew the cable. The information had been in our
  hands all along.

  🔑 **One cause, two symptoms.** The Overview's charge-window chip — *"Charge 01:50 – 12:00"*, built
  for **@rop12770** himself back in
  [discussion #173](https://github.com/ProtossBlaster/leapmotor-mate/discussions/173) — lives inside
  the "cable connected" branch, so the same unread code had been hiding the very answer he asked us
  for: *why isn't it charging?*

  ⚠️ **Plugged is not charging.** The cable is what keeps a session OPEN, so counting 4 as connected
  without more would have left the session that ended at 19:10 running until the window opened at
  01:50. A charge postponed to its window is now flagged as such, and the two places that turn "the
  cable is in" into "a charge is running" — the state machine, and the resume-or-close on restart —
  skip it. Entering a charge was already gated on the charging status, so 4 alone never opens one.

  ⚠️ And 4 is **not** treated as "never charging": that was tried and reverted before shipping. It
  has only ever been measured at rest, and nobody has watched a car whose window OPENS while the code
  stays at 4 — assuming it relabels itself would have dropped an entire scheduled charge in silence.
  The current decides, as it does for every other code.

## [3.10.3] — 2026-08-09

**BetaTester, range-extender only — one number that answers "plug in here, or just burn petrol?"**

### Added

- ⚖️ **Break-even price per kWh**, on the Statistics page. Asked for by **@ebagnoli**
  (BetaTester #13): *"the €/kWh cost of electricity at the charger should appear somewhere, so that
  charging works out cheaper than the range-extender's petrol"*. He charges at home off solar
  surplus, so his own answer is "always" — the number he needs is for standing in front of a public
  column with fuel in the tank.

      break-even €/kWh = (€/L × L/100 km with the generator) ÷ (kWh/100 km driving electric)

  On his own history: 15.0 kWh/100 km electric, 5.5 L/100 km on the generator, his own blended pump
  price of 1.75 €/L → **0.643 €/kWh**. Below that, charge; above it, the petrol is cheaper.

  🔴 **Each rate is measured on its OWN kilometres**, and that is the whole difficulty. Mate already
  publishes a kWh/100 km and an L/100 km side by side — both divided by the *whole* distance, which
  is exactly right for "what did the driving cost me" and exactly wrong here. Reusing them would
  have dragged the petrol figure down by every electric kilometre and answered a different question
  under numbers that look like the right ones.

  The price is the blend of **his own refuels**, never a pump price we invented, and the card says
  nothing at all when a half is missing — no generator kilometres, no refuel, no electric trips.
  When a side rests on less than 100 km it still answers, and **says so**: on his history the petrol
  half is 46 km, one trip, and a bare number would have carried none of that.

  In all seven languages, verified on the served page rather than in the files.

## [3.10.2] — 2026-08-09

**A quiet stretch in the car's reporting made the battery look older than it is — and a parked
stop that produced no bar now says why.**

Both reported by **@riri19** ([#241](https://github.com/ProtossBlaster/leapmotor-mate/issues/241)),
who attached the diagnostics bundle with the report.

### Fixed

- 🔋 **Battery health read low when the car went quiet mid-charge.** Capacity is estimated as
  *energy ÷ SoC risen*. The energy is integrated over the readings the car sent and — rightly —
  **skips any gap longer than 15 minutes**: nobody knows what the charger did while the car was
  silent, and integrating across it would invent kilowatt-hours. But the SoC side counted the
  **whole** rise, gap included. Two halves of the same fraction measured over different windows, so
  every quiet minute pushed the estimate down.

  Measured on one identical charge — 32 kWh into a 67.1 kWh pack — with a single hole in the middle:

  | hole | before | after |
  |---|---|---|
  | none | 100.4 % | 100.4 % |
  | 30 min | 91.0 % | 100.5 % |
  | 60 min | 81.6 % | 100.4 % |
  | 120 min | **62.8 %** | 100.7 % |

  The SoC rise is now accumulated **only across the intervals whose energy was counted**, so a gap
  shrinks both halves together. The 15-minute guard is unchanged — no energy is invented inside a
  gap; it simply stops being charged to the denominator.

  ⚠️ **Nothing moves on a healthy connection.** Checked against a real database before shipping:
  thirteen points before, thirteen after, identical to a tenth. Where your car reports normally you
  will see no change at all; where it went quiet, past points will read higher — which is the point.

### Added

- 🅿️ **The diagnostics bundle now lists the parked stops that produced NO bar, and why.** It could
  only ever show the ones that had been accepted, so "the chart stops on the 5th" had no follow-up
  question: a stop the car reported flat and a stop that never happened looked identical from the
  outside. Each rejection now carries its reason — **short** (under your own minimum-hours
  setting), **flat** (the SoC never moved in the readings), **below_noise_floor** (it moved less
  than 0.2 %), or **woke_driving**.

  That last one is what #241's second half turned out to be. A sleeping car reports a **frozen**
  SoC; the real drain only shows on the first fresh reading. When that reading arrives with the car
  **already moving**, the stop is closed on the odometer and its drop is discarded — correctly,
  because it now contains driving consumption and counting it as standby would overstate the drain.
  The refusal was right; the silence was not. On a car whose cloud only refreshes once it is
  driving, this is every stop, and the chart simply stops.

  Read on a real database: the charted bars are **unchanged** (38 before, 38 after), and 135
  previously invisible stops are now named.

## [3.10.1] — 2026-08-09

**The Charges page could go blank and stay blank. It can't any more — and the "to confirm" banner
now takes you to the charge.**

### Fixed

- ⚡ **The charge history disappearing, and never coming back** ([#240](https://github.com/ProtossBlaster/leapmotor-mate/issues/240),
  reported by **@Ng-EY**). The Charges page showed the banner — *"1 charge to confirm"* — above an
  empty space where the calendar should be. Reloading didn't help. Typing a date into the search
  brought the charge straight back, which made it look like the data was fine and only the page was
  wrong. It was.

  Clicking a day on the calendar is **remembered**, so a reload puts you back where you were. From
  that moment every request for that month asked for the day as well — and a request that carries a
  day renders the day's cards **inside the same response**. Those cards print a price, in your
  currency, and the calendar was the one place that never passed the currency along. The result was
  a **500**: the browser asks for the history, the server fails, and htmx — correctly — swaps
  nothing in. So the placeholder stayed, with no error and nothing to click. And because the
  remembered day was re-sent every time, it stayed broken across reloads.

  On an install where the remembered day sat in an **earlier** month, the current month drew fine
  and only paging back was dead — the same defect wearing a different face.

- 🩹 **A block that fails to load now says so.** Sixteen parts of Mate, across eight pages, fill
  themselves from a separate request after the page opens. htmx deliberately changes nothing when
  such a request fails, which is right — but it left those blocks silent, showing whatever they had.
  Any of them that now fails puts a short line under itself with the status code and a **Try again**
  button, instead of an empty space. It never replaces what you are reading: a failed month-flip
  won't wipe the month already on screen.

- 📅 **All four calendars are drawn by the server** — charges, trips, refuels and wallbox. Each
  month grid is in the page as it arrives, rather than depending on a second request nobody could
  see fail. The request still runs — it is what re-opens the day you had open, and what a
  `?highlight=` link scrolls to — but the page no longer *needs* it to come back.

### Changed

- ❓ **The "to confirm" banner is now a link.** It used to announce a charge without a type and
  leave you to work out which day of the calendar it was hiding on. It now takes you straight to
  the charge, opened on its own day and marked. This was the reporter's actual complaint, underneath
  the 500.
- 🔤 **One charge is no longer announced in the plural** — *"1 charge to confirm"* rather than
  *"1 charge(s)"*, and each of the seven languages says it its own way instead of borrowing an
  English bracket-s.

### Internal

- The test that guards the currency across every page followed `include` **one level** and read
  contexts only where they were written as a literal. The defect above went through both gaps. It
  now closes the include chain to a fixed point and reads the context with `ast` — dict, helper
  call, or a name assembled above — and a context shape it cannot read is a failure, not a pass.
- **All four calendars are now tested with a day open**, not just the one that broke: charges,
  trips, refuels and wallbox each render their day drawer through the real route against a seeded
  database, and each check compares the response *with* the day open against the one without — a
  test that only asked "did it render" was green on a wallbox drawer that never opened at all. The
  list of calendars is read off the templates, so a fifth one cannot be added without a test.

## [3.10.0] — 2026-08-08

**Two Leapmotors, one Mate — and the T03 can finally switch its air conditioning off.**

### Added

- 🚗 **Two cars on one account, in a single install.** Mate has been one car, one instance since the
  beginning, and that was the last big limitation left. If two Leapmotors share your Leapmotor
  account, they now share one Mate: **one poller, one database, one session against the cloud**
  instead of two installs quietly kicking each other off it.

  The cars are polled one after the other, each on its own cadence — a car asleep in the garage
  doesn't slow down the one that's driving — and a failure on one is contained so it can't stop the
  other.

  A **car picker** appears in the header, and only **from the second car onwards**: if you have one
  Leapmotor you will not see a single thing change, which was a hard requirement rather than a nice
  goal. Everything follows that one choice — the Overview, Statistics, trips, charges, the monthly
  report, the commands the model is allowed, and the Home Assistant entities. Your choice sticks;
  if a car ever disappears from the account, Mate falls back to the first one rather than showing
  you an empty interface.

  Settings — prices, currency, time zone, home location — stay shared, because they genuinely don't
  differ between two cars in one household. What moved onto the car is only what is a property of
  the car: its battery capacity, its PIN, whether it's a range-extender, what it can be commanded to
  do, and which sensors it actually has.

  Designed with **@cookingeek** (#186), who has been running two instances by hand and answered the
  three questions I couldn't answer alone.

  ⚠️ **Only ever tested on a synthetic two-car setup so far.** Nobody has yet run this with two real
  cars on one real account over days — the tester's second Leapmotor arrives shortly. Single-car
  installs are unaffected by design and by test.

- 🔑 **The operation PIN belongs to the car.** Two Leapmotors can want different four digits, and
  until now Mate stored one PIN for the whole install. Every command to the second car would have
  failed — and **only the commands**, because reads carry no PIN, so the pages would have looked
  perfectly healthy while the buttons quietly did nothing.

  Each car can now have its own, set from **Settings → Vehicle** (the field gains a car picker from
  the second car onwards). Leave it on "all cars" and nothing changes: a car without its own PIN
  falls back to the install-wide one, which is what every install alive today has.

  Found by **@cookingeek** reading the design, before his second car had even arrived.

### Fixed

- 🚗 **Eleven places where the second car would have shown, or written, the first car's data.**
  Switching car has to mean *everything* switches, so every read and write that touches a car was
  gone through one at a time rather than sampled — 104 in the web, 40 in the poller.

  What it turned up, and what each would have looked like:

  - **A Better Route Planner received both cars as one.** The token was one per install, sent from
    inside the per-car loop, so two cars pushed position, SoC and speed into the same ABRP vehicle,
    interleaved. Each car now has its own token (ABRP works that way too); a car with none sends
    nothing rather than borrowing the other's.
  - **A charge typed by hand, a fuel purchase, and a change of battery capacity all landed on the
    first car** whatever car was on screen — the capacity one being an ~80% error on everything
    derived from a percentage.
  - **The research signals** — count, latest values and export — read both cars at once, so the
    newest row won and the dashboard could show the fuel level of the car you were *not* looking at.
  - **Two trips of two different cars could be merged into one**, putting one car's kilometres and
    energy inside a trip that never made them.
  - **The charging plan and the remembered GPS hemisphere** are learned from each car; kept in one
    key, the car polled last overwrote the other. The second of those is the defect that has come
    back five times — a car drawn out at sea.
  - **A command sent to one car sped up polling for both.**

  Verified on a two-car install as well as by test: two cars seeded differently in every measurable
  way, fifteen pages compared in both directions, no trace of one car on the other's pages.

  ⚠️ Everything above falls back to the shared value when a car has none of its own, so a single-car
  install — every install today — behaves exactly as it did.



- 🚗 **Three defects that had nothing to do with two cars, and were found by building for them:**
  - **Pre-heating warmed the wrong car.** The ready automation sent its command to the *first*
    vehicle whatever car the data belonged to, and its "already fired" state was a single value
    shared between them.
  - **Battery capacity ignored which car you were looking at.** The per-car figure has been in the
    database since v2.2.0, but only the *writes* used it — so a T03's 36 kWh pack could be reasoned
    about as 65. That is **+80% on everything derived from a battery percentage**: trip energy,
    charge energy, costs, efficiency.
  - **The poller's main loop had no test at all** — two hundred lines that record every trip and
    every charge of every install, and the whole suite could go green with them broken.
  Also: the range-extender flag was one flag for the account rather than one per car (on a mixed
  household that put the REEV pages on the electric car and withheld the battery-derived figures
  from the car they are correct for), and the second car's Home Assistant entities never appeared.

- 🌡 **"A/C off" now actually switches the air conditioning off on the T03** (#67). On every other
  model Mate sends a bare `operate=off`, which works and is untouched. The T03 ignores that form —
  and ignores `operate=close` too, which is what Mate had been sending it. What the car honours is
  `operate=off` **inside the full climate payload**, with all seven fields present. On the T03 it is
  the *shape* of the command that decides, not the value.

  This had been open for months, and not only here: every integration in the ecosystem sent one of
  the two forms the T03 ignores (kerniger/leapmotor-ha#28, markoceri/leapmotor-api#9). It was
  genuinely hard to see, because the cloud answers "accepted" to all three — so no log, on anybody's
  machine, could tell a command that worked from one the car threw away. The only instrument that
  ever worked was somebody watching their own car.

  **Found and verified on-car by [@derekzoli](https://github.com/derekzoli)**, who tested the
  candidates on his own T03 on 6-7 August, confirmed the winner by re-reading the vehicle state a few
  seconds later and watching the A/C actually stop — and then brought the answer back to the report
  we had opened at markoceri/leapmotor-api#9, rather than keeping it. He also built his own app,
  [MyLeapCar](https://github.com/derekzoli/myleapcar), on the way there. Mate's own hunt (seven
  candidate payloads, offered to T03 owners since v2.1.6) had searched the same grid in the wrong
  direction and missed it; that page is now retired, along with its buttons.

  Owners of a B10, C10 or B05: nothing changes for you. This is the T03 only.

  ⛔ Still unsolved, and honest about it: **air direction** on the T03. He reports every attempt to
  set it ignored, and Mate has never offered the control.

## [3.9.1] — 2026-08-08

**A temperature the car never sends is absent, not zero degrees — and its Home Assistant entity goes
with it.**

### Fixed

- 🌡️ **A sensor the car does not have read 0 °C.** @staffhotel-beep (#144) on a European T03: the
  cabin and battery temperatures had been empty all summer. Mate read the three temperature signals
  as *"the value, or zero"*, so a signal the car never sends became **0.0** — and in a week at 40 °C
  that number is not ambiguous, it is absurd. It did not stay on the page either: it went to **A
  Better Route Planner** as a real cabin reading, and to the **Prepare vehicle** temperature
  condition, where *"only pre-heat below 5 °C"* was therefore satisfied on **every poll, all year
  round**, on any car without that sensor. Silence is now silence; an unknown temperature does not
  fire the automation and says so in the log.
- 🌡️ **A real 0 °C was thrown away.** The mirror of the same defect: the A/C-target and battery rows
  decided by truthiness, so a battery pack genuinely at **0.0 °C** — the one reading its owner is
  watching for in winter — printed "—". All three rows now tell absent from zero.
- 🙈 **A row that says "—" for ever still promises a number.** A temperature sensor this car has
  **never once reported** is no longer shown at all. Measured, not guessed from the model: it takes
  50 polls with nothing in them before Mate says anything, asked over the recent window — so a fresh
  install shows every row, and a sensor that starts answering is back within hours. With rows hidden
  the half-width grid was left open, so the odometer sat alone in a narrow column with its value
  wrapped onto two lines; it now takes the full width exactly when the count would be odd.
- 🏠 **…and the Home Assistant entity is removed too.** The discussion is titled *"Unsupported
  entities for T03 model"* — the complaint was about the entities, and hiding a row in the web UI
  while leaving a sensor stuck on `unknown` answered the other half. A temperature entity whose
  sensor the car never reports is now dropped by the same mechanism the model-absent comfort entities
  have used since v2.6.1 (a retained empty discovery config). Re-checked on every poll rather than
  only at discovery, because the answer needs 50 polls of evidence and discovery runs once per
  connection: the removal lands when the evidence does, without a restart, and the entity returns if
  the readings do.
- 🔍 **The diagnostics bundle can answer this next time.** It now counts each temperature over the
  retained polls and prints `cabin NEVER (0 of 88000) — the car does not send this signal`. The
  bundle @staffhotel-beep was asked for could not answer the question it was asked for: 77 000 lines,
  four mentions of "temp", not one of them a reading.

Thanks to **@staffhotel-beep** for the report, the bundle, and the discussion title that turned out
to name the half that had been missed.

## [3.9.0] — 2026-08-08

**Charges can carry kilometres, and the cost per 100 km stops mixing two different periods.**

### Fixed

- 💶 **Cost per 100 km divided money from months by kilometres from one afternoon.** @nico89612
  (#237) typed 152 charging sessions into Mate from before he installed it and the Statistics page
  read **4838.43 €/100 km**. Nothing was rounded wrong: the euros were summed over the *entire*
  archive while the divisor came from the recorded trips alone, so the two halves of one figure
  described different years. The same card's kWh/100 km half was already windowed correctly, which
  is how the split was found — his 19.2 reproduces to the decimal.

  The euros are now counted over the window the **kilometres** cover. A charge that ended before
  the first recorded trip has no kilometres of its own to be divided by and contributes nothing;
  a charge made *after* the last trip keeps its money, because those kilometres arrive tomorrow.
  Measured on a real B10 with ten weeks of history: **6.20 → 6.20 €/100 km, not one charge out of
  window.** It only moves for someone whose charges reach back further than Mate's kilometres do.

- 💱 **The price box was labelled in euros for everybody.** Mate offers fourteen currencies. The
  "add a past charge" form printed the currency's whole metadata record instead of its symbol
  (on screen: `{'name': 'I`) — there since that form was written — and the two type-and-price
  boxes had `€` written into them by hand. All three now read in your own money.

### Added

- 🛣 **A charge remembers the odometer it started at.** Written by the poller from the same frame
  that opens the session, back-filled once from the positions already on disk (**26 of 28 recovered
  on a real car, to the second**, because both rows come from one poll), and — the part nothing
  else could give you — **typable by hand** on the manual form and as the last column of the CSV.

  That last piece is the only way a session from before Mate existed can carry kilometres at all:
  no poll of it was ever made, and nothing in the database can invent them. Fill the column in and
  the cost per 100 km is measured against the car's own counter, over exactly the period the
  charges cover — brim to brim, the way a driver measures fuel. It works even with **no recorded
  trips at all**, which is the case for anyone who kept a notebook and installs Mate months later.

  Mate picks whichever basis prices **more of what you actually spent**, and says which one it used
  under the figure. On an ordinary history the trips win and nothing changes.

- 🛣 **How far the car went between one charge and the next**, on the Charges page, from the car's
  own odometer. Shown only where both readings exist and only where it actually moved: two sessions
  the same afternoon say nothing rather than print a zero.

### Changed

- 📥 **Re-importing a CSV fills in the sessions already there instead of adding them again.** It
  used to insert every clean line unconditionally, so importing the same file twice doubled the
  archive — and the money with it — without a word. A line matching a session already recorded
  (same moment, same energy) now completes it. Deliberately narrow: it writes the odometer and
  **nothing else**, so a cost Mate computed from a real charging curve is never overwritten.

- 📏 **"Total distance" is now "Distance of recorded trips"**, on Statistics, Trips and the monthly
  report. It has always been the sum of the finished trips and nothing else, but the word *total*
  read as the car's lifetime counter — the more so now that the cost card can divide by exactly
  that.

## [3.8.8] — 2026-08-07

**The car in the sea puts itself back, and a trip stops waiting on a cloud that went quiet.**

### Fixed

- 🗺 **A car plotted in the wrong hemisphere now corrects itself at startup.** The remembered
  north/south and east/west sign was a **single stored value**, and before v3.8.6 one frame that
  arrived with its minus dropped overwrote it. From that moment every restart primed from the
  poisoned copy, so the car stayed mirrored for good — the guard added in v3.8.6 stops the sign
  being *learned* wrongly, but it cannot un-learn one already stored.

  Mate now re-derives that sign from **the car's own position history** instead of trusting the
  stored value blindly. You cannot have driven a fortnight of kilometres somewhere you have never
  been, so the history outvotes any single frame by construction — and it is already on disk, which
  is why this needs no button and asks you nothing. **Restarting Mate is the repair.**

  Measured on @rop12770's install (#232): eighteen trip starts logged at longitude **−7.2** between
  1 and 5 August, against a stored sign of **+1** written by one bad frame after the last of them.
  That is why his trips read correctly while only the marker sat out at sea — the poller had the
  right positions on disk and the web was applying the wrong sign over the top of them.

  🔑 The history is counted in **places, not rows**. While the car sleeps the cloud re-serves one
  frozen frame and Mate still records it every 30 seconds: he banked ~1900 identical rows over
  sixteen hours. Counted as rows, one stuck frame would outvote real driving 60:1 and confirm the
  very bug this undoes. A sign is only adopted on a near-unanimous majority, so a car that genuinely
  moved across the line is left alone rather than guessed at.

- 🚗 **A trip no longer stays open because the cloud stopped talking.** Ending a trip needs the
  cloud to *say* gear P, six times running. When it instead freezes on a frame that says D — it
  re-serves the last frame it holds, indefinitely — those readings never come: the car is parked in
  the drive and Mate reports **Driving** for the rest of the day, with the parked hours swallowed
  into the trip. Reported by @riri19 (#233): *"stuck in Drive mode for hours, sometimes all day."*

  After half an hour of one repeated frame, the trip is now given up on and closed.

  🔑 The threshold was measured, not chosen. On his 40 246 polls: while the car is genuinely moving
  a fresh frame arrives every **18 seconds** (median), 36 s at the 95th percentile and **9.4 min at
  the 99th** — even on a link as poor as his — and of the **17** frozen stretches longer than ten
  minutes, **all 17** ended with the odometer on the exact value it started at. Not one was a car
  still driving. Replaying his twelve days through the fix: the longest trip falls from **11 hours
  24 minutes to 61 minutes**, the three multi-hour trips disappear, and the total distance moves by
  1 km out of 793.

- 📍 **A trip that opened late now starts where the car set off, not where the signal came back.**
  v2.3.0 already moved the distance and the energy back over kilometres driven while the cloud was
  dark (#118). The start POSITION was deliberately left behind, on the grounds that a frozen frame's
  GPS is often 0,0 and would plant the trip in the Gulf of Guinea. That threw away the good
  coordinates along with the bad: @riri19's 19 km drive opened **5 km from home** while the frame in
  Mate's hand still said *parked at home* and was 32 minutes stale. Mate now anchors to the last
  position it actually held — and simply never stores a zero as one, so the Gulf of Guinea case is
  unchanged.

- 🕰 **"Last seen" now says when the CAR reported, not when Mate wrote a row.** The poller records a
  position every 30 seconds while parked, and it keeps recording them while the cloud re-serves one
  frozen frame — so the row is always seconds old, whatever is in it. The Overview map popup and the
  status card both showed that row time: **"22 seconds ago"** over a marker that had not moved in
  sixteen hours. It is the figure @rop12770 was looking at in #232 while asking why his car was in
  the sea, and on a link like @riri19's (#233) the true age is **78 minutes at the median**.

  Nothing new is computed — the honest age was already measured from the frame's own clock on every
  poll, and simply never reached the page. The amber mark that flags a link lost mid-drive stays,
  now as a colour on that one figure rather than a second number beside it.

- 🔁 **The Trips and Statistics pages no longer reload themselves out from under you.** Pick last
  month, read for half a minute, and the idle auto-refresh put you back on the current one — with
  the statistics period reset too. Reported by @michapr (#236).

  The month and the period are held in the page, not in the address, so `location.reload()` could
  never restore them. The idle guards added for @pdifeo in beta [#22](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/22) could not help either: they
  protect a field being typed in or a mode switched on, and a choice already made and now being
  *read* looks exactly like an idle page. Both pages show history, so there is nothing live for a
  reload to fetch and the refresh button is in the header. Every other page keeps its 30 seconds.

### Changed

- 🩺 **The diagnostics bundle now shows the *sign* of each GPS signal, not just that it arrived.**
  The line tested only for "not empty", so a signal that arrives as **zero** — the axis saying
  nothing — was listed as present alongside real ones. It reads `2:−, 3:+, 3724:+` now, with `zero`
  called out by name. One character per axis: it narrows the car to a quadrant, which the remembered
  sign printed directly beneath it already did, and no magnitude ever appears. Chasing #232 through
  the old line cost two hours and produced a diagnosis that was wrong.

## [3.8.7] — 2026-08-07

**A merged drive that only ever counted the first tank.**

### Fixed

- ⛽ **A merged trip now counts every segment's petrol.** Join two drives and the detail page read
  the fuel percentages from the whole group but the **litres from the first segment alone** — one
  line apart in the code, and against the comment sitting directly above them. The litres win
  whenever the car's own millilitre counter is present, so everything the later segments burned was
  quietly dropped.

  Measured on a 30 + 30 km drive that burned **2.9 L**: the Trips list said **4.8 L/100km** and the
  trip's own page said **2.5** — one drive, two pages, two answers, and neither number looked wrong
  on its own. It is beta [#20](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/20) returning through the millilitre path added in v2.14.1, which the fix
  of the time never covered; merging writes only the link between the trips, so nothing rewrote the
  first segment's tank afterwards. Found while checking something else — nobody reported it.

- ⛽ **A number can no longer be torn from its unit.** In that same tile `⛽ 4.6 L · 6.8 L/100km`
  needs 140px of a 130px cell, so it wrapped **inside** the second figure and left `L/100km`
  stranded on its own line under a bare `6.8`. Smaller type is not the answer — a big real trip
  (`⛽ 12.4 L · 15.7 L/100km`) still overflows at 11px. Each figure is now unbreakable, so the line
  break falls **between** them and both stay whole.

### Added — BetaTester build

- ⚡ **The trip page shows the electric kWh/100 km on a generator drive.** The Trips list has
  printed it since the range-extender work; the trip's own page showed a dash for the very same
  drive. @michapr (beta [#27](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/27)): *"I think the 1.2 kWh are right. Because all other energy is coming
  from generator — counted by fuel usage."* He is right that the pair describes the drive: 1.2 kWh
  **and** 4.6 L, each over the whole distance.

  It sits beside the litres, marked in blue, and **not** in AVG CONSUMPTION — which keeps its dash.
  getEC counts what left the battery, and the generator drives the wheels without passing through
  the pack, so this figure and the consumption of a purely electric drive are not the same quantity
  and must not share a label. His own suggestion, and it is what the list already does.

## [3.8.6] — 2026-08-07

**A car that kept ending up in the sea, and a charge type that only ever spoke English.**

### Fixed

- 🧭 **GPS: your car stays where it is on the map.** @rop12770 (#232) is in Portugal and his C10 was
  plotted in the sea west of Sardinia — latitude right, longitude mirrored, and a marker **22
  seconds old**, so nothing to do with old history.

  That resolver had not changed since **v2.8.8**, so no update moved it. Reading it again found the
  hole the comment beside it denies:

  > *The memory is only ever written by a signed read, never by the fallback, so it can't be
  > polluted.*

  True only while the *signed* signal is actually signed. The cloud sends the coordinates twice —
  once carrying its sign, once as a bare magnitude — and Mate believed the signed slot whenever it
  was non-zero, with **no sanity check at all**. A single frame arriving there without its minus
  teaches the wrong hemisphere; the poller then writes that conclusion to the database, so it
  survives the restart and both readers mirror the car from then on. The guard built to survive a
  *missing* sign had no defence against a *wrong* one.

  🔑 The physics it was missing: **a car cannot teleport across the line.** Driving to the other
  side means passing through zero, so a real crossing is always seen near the meridian — while a
  dropped sign shows up at full magnitude, 8.6° W becoming 8.6° E, 1720 km between two polls.

  So a remembered hemisphere now changes only on evidence: **at once within 1°** of the line, where
  crossing is ordinary, and otherwise only after **ten consecutive polls** say the same thing. One
  good frame in between resets the count, so a cloud that flickers never accumulates. A car
  genuinely shipped across the meridian with its SIM dark re-registers within minutes of coming
  back; a glitch never gets there.

  🔑 And the guard is **one-directional**, because it has to be: a lost sign can only ever surface
  as a *positive* number — the signals it gets confused with are magnitudes, with no minus to lose.
  A negative reading is therefore proof, not suspicion, and passes straight through. Doubting it
  would have stranded any car moving west or south for ten polls and bought nothing.

  The poller now says so in its own log, **with no coordinate in the line** — that log ships inside
  the diagnostics bundle, which exists to be attached in public.

  ⚠️ What this does **not** do: repair an install whose remembered sign has already flipped. That
  one heals on the first properly signed frame the cloud sends. #232 stays open until its bundle
  arrives — the guard closes the way in, it does not yet prove it was the way used.

- 🌍 **The charge type now reads in your own language.** @konrad300 (#210) opened Mate in Polish
  and found the Costs page, the charge badge, the type dropdown and the search filter all saying
  **"Home"** — while the monthly report, on the same screen, called the same thing **"Dom"**.

  The comment sitting above those labels was the part that was wrong:

  > *Labels are intentionally language-neutral (international loanwords + universal electrical
  > acronyms) so they never need translating across UI languages.*

  **AC, DC and HPC are acronyms and stay.** *Home*, *FREE* and *Manual* are ordinary English words,
  and the app was not only contradicting its own report — it was contradicting **its own manual**,
  which has said *Casa*, *Domicile* and *Zuhause* in three languages all along.

  🔑 Two of the three words already existed, translated by native speakers, and are reused rather
  than duplicated: `report_home` — the monthly report's own *Dom*, the very word that exposed the
  contradiction — and `charge_free`. Only *Manual* needed adding, in all seven languages.

  ⚠️ **Three copies, in three languages**: a Python dict, a Jinja tuple, and a JavaScript object in
  the same page. A fix that caught only the one he reported would have been the same defect
  returning on a different screen. Translated at the source, so the nine routes that hand the types
  to a template needed no change and cannot drift apart later.

  We answered *"PR very welcome, please go ahead"* and left him the map of the keys to reuse. Six
  days on it had not arrived, so it is done here — with the thanks it deserves for a report precise
  enough to be actioned without a single question back.

### Changed

- The four user manuals stopped declaring **v3.6.0**. Their contents have been updated with every
  release since; only the line at the top saying which version they describe had been left behind —
  which is the exact defect the rule about shipping documentation was written to prevent.

## [3.8.5] — 2026-08-06

**A diagnostics release: it changes nothing you look at, and a great deal about what can be
answered when something goes wrong.**

@adoewa (#230) charged from 49.8 % to 90.0 % over three and a quarter hours and Mate opened no
session. His bundle could prove the car was **online** the whole time — 185 polls carrying 185
different frames, two seconds old, against the 477 polls earlier the same night that all carried
**one** frame aged up to nine hours — and it could prove the charge happened. It could not say
**why** nothing opened, because the three signals that decide it were nowhere to be found: they live
in the database, one row per poll, and the bundle carried them as a single snapshot taken ten hours
after the cable came out.

Measured before changing anything, across every bundle we hold — 7 bundles, 5 cars, 88 car-days:
**35 charges taken while parked, 34 recorded, 1 lost.** So this is not a fix for something that
bites everyone. It is the diagnostics carrying enough that the next one is answerable at all,
whoever it happens to.

**And then he found it himself, in his own Settings, while we were reading his logs**: *"I
discovered that the Charge detection was on 14.5 A. Previous I have set this to 2A."* Below that
floor Mate does not call it charging — and a home AC charge moves the pack at **11-12 A**, so
anything above ~11 A hides **every** home charge, silently, with the car perfectly online. It
explains all of it: why his 28-29 July charges were recorded (the floor was still 2), and why he is
one case in 35.

The bundle reported the vampire-drain thresholds and **not that one**. It does now.

### Fixed
- 🔴 **The cost card was billing petrol that is still in the tank.** @michapr (beta [#25](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/25)) said the
  card "looks wrong"; asked which number, he pointed at **27.72 €/100 km**, of which **24.18 €** was
  fuel. Measured on the trips in his own bundle:

  | | |
  |---|---|
  | 9.60 L burned over 6 generator trips, 479 km | |
  | what **Mate itself** charges those trips | **18.54 €** = 3.87 €/100 km = **1.93 €/L** |
  | what the **card** charged | **116 €** = 24.18 €/100 km = **12.06 €/L** |

  1.93 €/L is a pump price; 12.06 is not. The card summed `fuel_purchases.total_cost` — every euro
  of every refuel — while ~50 of the ~60 litres he had bought were still in the tank, waiting to
  move the car on kilometres this window has never seen.

  Everywhere else Mate multiplies **litres burned × a blended €/L**, the lookup written for this
  same tester on beta [#11](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/11). So the Trips page said that petrol cost 18.54 € while the Statistics card
  said 116 €: two numbers under one word, same program, same period, same car. The card now uses the
  same figure. Two existing tests had to be corrected — they asserted the old behaviour, and were
  asserting the defect.

  ⚠️ `reev_actual_spend` still sums the purchases and is **right** to: that card answers "what did
  you buy", and a tank is paid for whether or not it has been burned. Two cards, two questions.

### Changed
- **On a range-extender the trip's fuel sits in the header**, beside the kWh — which is what a car
  with no tank already does with its one energy figure (@michapr, beta [#27](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/27)). Looking at the rendered
  page made his point sharper than his words: the header showed **12.4** as AVG CONSUMPTION and
  **12.4 kWh** as ENERGY USED, and the amber "Dual energy" box underneath opened with **12.4 kWh**
  again — the same number three times in one column, the third framed as a warning, for something
  that happens on every generator trip.

  The litres moved up, the block stopped repeating the electric figure, and it lost the amber border
  and the title: it was never a warning, it is the detail. **Nothing was dropped** — where the
  battery and the tank started and ended, how much of the electricity was paid for at a plug against
  how much the generator supplied, how far the generator drove, and what the electric number does
  and does not measure all still live there, because they live nowhere else.

- 🔴 **The nine sliders that change how Mate behaves no longer save the moment you let go.** Each
  sat inside a form with `hx-trigger="change"`, and a range input fires `change` on thumb-release —
  so a stray touch, or a finger dragging across one while scrolling a phone, wrote the new value
  immediately, with no confirmation and no trace. Among them: the charge-detection floor and the
  poll cadence, which are exactly the two @adoewa found moved.

  They now need a **Save** press, like the data-retention card already did. The slider still moves
  freely; nothing is written until you say so.

- **And every change to one of them is written down** — when, from what, to what. Not the language,
  not the currency, never a secret: only the ten settings that silently change what gets recorded.
  Saving a form that changed nothing records nothing, so the trail stays readable.

### Added
- **The behaviour settings are in the bundle**, with their defaults, the ones that differ marked,
  and the recorded changes underneath:

  ```
  10 behaviour settings · ⚠ 1 NOT at default
    ⚠ poll_driving   60   (default 10)
    changes recorded (1, newest first):
      2026-08-06T15:44  charge_detect_min_a: 14.5 → 2.0
  ```

  ⚠️ Two of those defaults were written wrong on the first pass — `soh_temp_min_c` as 10 against a
  real 15, `map_station_min_sessions` as 2 against a real 1 — which would have marked an untouched
  install as modified, the exact question the section exists to answer. They are now checked against
  the settings page itself, all ten of them, not one by hand.

- **The charge-detection floor is on the bundle's first page**, beside the poll cadence, with what
  it means: `Charge detect: min 14.5 A (default 2.0 — above ~11 A a home charge is never seen)`.
  The setting that decides whether a charge is seen at all was the one thing triage could not read.

- **The poller's log line says what it saw about charging**: whether the cable declared itself,
  whether Mate concluded it was charging, and the pack current. Three fields, ~20 bytes a line, on
  every poll. Nothing new is collected — the poller already held all three and simply never wrote
  them down. #230's line would have read `plug=0 chg=0 A=0.0` for 202 consecutive polls, and the
  question would have been closed in a minute instead of half an hour.

- **The bundle carries the charges and the trips themselves**, from the database, for the last
  15 days — the rows, not figures computed from them. Until now every "this trip is wrong" report
  was answered by reading 30 000 log lines and inferring what the table must contain.
  Charges keep a floor of 10 rows (charging weekly gives two in a fortnight); trips are capped at
  200 **and the file says so when it truncates** — a silently shortened list reads as "this is
  everything", which is how a correct file produces a wrong conclusion.

- **…and a section that lists every time the battery filled up while parked**, with what Mate could
  see at that moment: the cable, the decision, the current, and — the line the whole investigation
  turned on — whether the frames were arriving fresh or the cloud was repeating one old reading.
  It reads data **already recorded**, so it answers about the past without waiting for a recurrence.
  Charges that WERE recorded are listed beside the missed ones: a flagged line with no control group
  next to it is a coincidence, not a finding.

  ⚠️ Where `frame_ts` predates its own column the count says so rather than reporting "1 distinct
  frame" — on a real database 25 761 rows of 207 287 carry it, and the naive count called fifteen
  hours of perfect charging a dead zone. Absent is not repeated.

- Nothing locating anybody: no coordinates, no geohashes, no charge location name or URL, and none
  of the free-text notes a user may have put an address or a plate in. The header's promise of
  "GPS removed" stays true.

## [3.8.4] — 2026-08-06

### Fixed
- 🔴 **The Wallbox page showed 141.5 % efficiency — the car taking more than the wall gave.**
  @Wartopia (#229). The cause was not a bad reading: the page held **two copies** of the same
  arithmetic and they disagreed on one screen — 141.5 % on the tiles, 92.5 % on the calendar
  underneath.

  The tiles were written on **3 June**, when `ac_energy_kwh` did not exist and the only way to know
  the wall's kWh was to integrate the power sensor's Home Assistant history, session by session, on
  every page load. The column arrived on **9 June** and the poller has written the meter's own kWh
  onto the row ever since. Nobody moved the tiles onto it.

  All four views — tiles, calendar month, day drawer, sessions tree — now read the stored column
  through one function. A charge counts **only when it has both figures**: adding a battery kWh
  while adding no meter kWh is exactly what pushes the ratio over 100 %, and it is not rare —
  `finalize_charge` deliberately clears the meter figure when the counter ran away (#46) or stood
  still (#215), and every HOME charge from before the wallbox was configured has none at all. What
  gets left out is counted and can be said, never silently dropped.

  **Charges still in progress are excluded**: a session that is still arriving has no total to
  compare. And the page no longer fetches Home Assistant history at all, which is what made it take
  five to ten seconds to open.

- **A range-extender's L/100 km was divided by the generator trips alone.** @michapr (beta [#26](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/26)).
  v3.6.9 moved this figure onto the whole distance and the line was changed with it, but the query
  above it still selected only the trips whose tank had dropped — so the denominator could never see
  an electric kilometre. On his own history it read **5.85 L/100 km instead of 2.0**, against the
  **2.9** his car reports for its own window. The denominator had been corrected; the set of rows it
  summed over had not.

### Added
- **The Cost per 100 km card now also says how many kWh those 100 km took.** @michapr (beta [#25](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/25)),
  who worked the method out on his own history and cross-checked it two independent ways.

  It is a **closed-system balance**, not a sum of trips: *energy charged inside the window* minus
  *the net change in stored energy across it*. Trip-only figures miss every kWh that left the pack
  standing still, and only **71.8 %** of a bill reaches a trip at all (#207). A charge counts only
  if its own window sits entirely inside — one that ends before the first trip has already been
  absorbed into the starting percentage, and counting it would bill it twice.

  The same formula is right on both cars without a branch: what it returns is what left the pack
  **minus the generator's contribution**, which is the grid-derived half — and on a card that prices
  fuel separately that is exactly the number wanted. On a plain electric car the generator term is
  zero. A session with no energy figure makes the number a **floor**, and the card says so; the
  balance is never reported as `0.0`.

  ⚠️ It is labelled **"including standing time"**, in all seven languages, because the Trips header
  shows a kWh/100 km that means something different and the two are about 28 % apart.

- **What the generator itself drinks while it runs**, on the Statistics page beside the pair
  measured by the car. @michapr (beta [#26](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/26)) — *"certainly an interesting technical metric"*. On his
  history that is **15.2 L/100 km over the 63 km the generator drove**, against 2.0 over all 479.
  Same unit, same card, seven times apart, so it carries **"while running"** next to the figure.
  Range-extender and BetaTester only, gated on the data rather than on the markup.

- **The Trips period strip says what its efficiency covers** — *"over 452 km of 509 km"* with an ⓘ,
  and only when the two differ. @michapr (beta [#11](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/11)), in his wording rather than mine: a figure over
  452 km tells you nothing on its own about whether that slice is most of the window or a corner
  of it.

## [3.8.3] — 2026-08-06

### Fixed
- 🔴 **A database restored without its `secret.key` sent the ciphertext to Leapmotor as the
  password — until the account locked.** @Ng-EY (#227) restarted Docker clean, so `/data` was empty
  and Mate generated a fresh key; he then put his old database back, whose secrets belonged to the
  key that was gone. From his log, in fourteen minutes: `Generated new secret key`, then
  `could not be decrypted` **101 times**, then `Incorrect account or password`, then
  **`Password error limit has reached maximum`**.

  On a decrypt failure `decrypt` returned the **raw value**, so `enc:v1:gAAAA…` went out as a
  credential and the retries did the rest. Returning the raw value is what lets a legacy
  **plaintext** secret through, but that case returns earlier — in the failure branch the value is
  always ciphertext, and passing it on can only be mistaken for a password. It now returns empty.

  ⚠️ **Nothing is erased.** The stored setting keeps its ciphertext: put the right key back and
  every secret is readable again. Verified end to end — saved, key lost (reads empty), key
  restored (reads the password again).

- **The web now explains it, the way the poller always has.** `_check_decryption` has warned about
  exactly this since the encryption landed — in the *poller's* log. He was reading the web's, and
  got 101 identical generic lines and no instruction. At startup, before anything tries to log in,
  the web now says which secrets it cannot read, names `/data/secret.key`, and adds that trips and
  charges are not encrypted and are unaffected. Once per process, not once per import.

### Changed
- **On a range extender the trip's ENERGY USED tile shows getEC and nothing derived from SoC.**
  @michapr (beta [#11](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/11)): *"we should only show the getEC value that is actually used for the
  calculation"*. Both branches it chose between were SoC arithmetic — `battery_net_kwh` is
  ΔSoC × capacity, and the consumption figure is efficiency × distance where the efficiency itself
  came from ΔSoC when the trip closed. On a series hybrid the generator refills the pack mid-drive,
  so neither measures what the driving used, and neither is what the money beside them is billed
  on. Where getEC has not arrived yet the tile shows a dash rather than falling back. A plain
  electric car is untouched.

## [3.8.2] — 2026-08-05

### Fixed
- 🔴 **The diagnostics bundle said `wallbox=True` to people who had switched the wallbox off.** That
  field was never the switch: it read `ha_url or SUPERVISOR_TOKEN`, which under the Home Assistant
  add-on is true for everybody, ticked or not. @wlighter told us he had turned the feature off
  months ago; we read the line, believed the name, and told him in public that he had not
  (discussion #226). He was right, and his case was the field proof of the v3.8.0 defect — the one
  fact that would have settled it was the only one the bundle did not carry.

  `wallbox=` is now **the tick**, and `ha=` is the reachability the old field actually measured.
  Both are printed, and the Settings badge row shows them apart.

### Changed
- **A range-extender trip now shows the TOTAL energy that left the battery, not the driving share.**
  The card read `ec_driving`, while the money printed beside it is billed on `ec_kwh` — so
  @michapr's trips showed "1.7 kWh" over a cost worked out on 2.0 (beta [#11](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/11)). Two electric figures
  on one card, and the one on show was not the one paid. A plain electric car has always been
  billed on the total; a range-extender now matches it. On @michapr's history the figure goes from
  3.2 to **2.71 kWh/100 km**.

- 🔴 **The day and month strips divided that energy by kilometres it was never measured over.**
  getEC is not on every trip: it arrives with a later poll and is simply absent on anything recorded
  before the feature existed. On the B10 this was measured against, **123 finished trips of 323
  carry no reading — 1016 km of 1824**. Summing the kWh we have and dividing by every kilometre
  driven printed a consumption at a fraction of the truth: May 2026 would have read **0.4 kWh/100
  km** instead of 20.0. The electric figure now divides by the distance it was actually metered
  over, and the strip carries that distance. The litres keep dividing by everything, because the
  tank gauge is on every trip — the two denominators differ on purpose.

### Added
- **The beta bundle now carries the trips** (BetaTester builds only). It held the raw signals and
  the logbook, which say what the car sent but not what Mate made of it — so every open
  range-extender question could only be answered by reasoning from our own electric car. `trips.csv`
  brings the battery's two measurements (`ec_kwh`, `ec_driving`, start/end SoC) and the tank's two
  (millilitre counter and percentage gauge) side by side, plus what Mate computed from them.
  `meta.json` says how many trips carry a getEC reading at all.

  What travels is an **allow-list**, not "everything except the coordinates": `get_trips` is
  `SELECT *`, so a column added next month would otherwise ship on its own. No coordinate, geohash
  or address leaves the machine, and the bundle stays sealed to the maintainer's key.

## [3.8.1] — 2026-08-05

### Fixed
- 🔴 **A range-extender's day and month showed the electric half of the bill.** @michapr's 28 July
  read **129 km · 8.3 L · 0.08 €**, and his July strip **416 km · 9.6 L · 9.02 €** against the ~33 €
  he worked out by hand (beta [#11](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/11)).

  The totals folded `cost`, which is the **electric** line by design — the petrol has a field of its
  own so every existing reader keeps the meaning it had. And a generator trip carries no efficiency,
  so it has no electric cost at all: the tank emptied into a total that never saw it. The trip
  detail page had the whole figure and nothing else did.

  Nothing about this was new. What changed is that we put the LITRES next to it in v3.6.9 — "8.3 L"
  beside "0.08 €" is unreadable in a way that "0.08 €" alone never was. The defect did not appear,
  it became visible.

  The trips the calendar folds now carry the **fuel cost** too, priced exactly as the detail page
  prices it: litres × the tank's blended €/L at the trip's start, with the rate timeline built once
  per query rather than replayed per trip. A BEV is untouched — there is no fuel cost there to add.

- **The month strip and the day header divided their two "per 100 km" figures by different
  distances.** The consumption pill is the efficiency the car MEASURED, and Mate stores none for a
  generator trip — so it covered the battery-driven part of the window while the litres beside it
  covered all of it. Fixed in the Trips header in v3.7.0 and left in these two; @michapr found both
  within two hours (beta [#24](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/24) → beta [#11](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/11)). On a range-extender all three now divide by the same
  kilometres, from the same SoC-based rule the Statistics page uses, so the pages cannot disagree.
  A BEV keeps the measured efficiency everywhere.

## [3.8.0] — 2026-08-05

### Added
- **Change the car's PIN without unlinking the account.** *Settings → Vehicle*, under the account
  address: **Operation PIN**, typed twice with an eye to read it back. Until now the PIN was written
  in exactly two places — the setup wizard saved it and Logout cleared it — so four digits changed
  on the car meant signing out of the Leapmotor account and walking the whole wizard again. Nothing
  was lost doing that (history is keyed by VIN), but Logout is a frightening button to press for a
  typo. Asked for by **@alextchao** (#225).

  Typed twice for the same reason as the access password (#214): a PIN stored with a typo does not
  fail here, it fails **at the car**, later, with an error that names no digit. It takes effect at
  once for both processes — the web command session is dropped so the next command re-authenticates,
  and the poller already restarts itself when the stored login changes.

### Fixed
- 🔴 **Switching the wallbox OFF now switches it off.** The toggle in Settings hid the wallbox page,
  the session-energy line and the chart overlay — and stopped there. `get_live()` never looked at
  it, and neither did the poller, whose only gate asks *where* a charge happened, never *whether the
  feature is on*. So with the toggle off and a mapping still saved, every poll kept reading the
  meter into `ac_energy_kwh`.

  That is not a display detail: a home charge is **billed** on that column whenever it is set, it
  becomes the charge's energy everywhere, and the card prints "🔌 wallbox (billed)". An owner who
  turned the feature off went on being charged at a meter he had switched away from.

  The gate now sits in the one place both processes read through. Off means there is no wallbox
  data, for anybody — and the saved mapping is left untouched, so switching back on needs no
  remapping.

## [3.7.0] — 2026-08-05

### Added
- **What 100 km actually cost you.** A new card on the Statistics page: **every euro spent, divided
  by every kilometre driven**. No price per kWh is computed and none is shown — the euros are added
  up and divided by the distance, so the electricity that moved the car nowhere (climate,
  preconditioning, the on-board charger's own losses) is in there too. On a range-extender the
  petrol is added beside the electricity, and the split says which is which.

  It follows your units — with miles it becomes *per 100 mi*, and the number grows, because
  100 miles are 160.9 km. Anything Mate was not told about is named underneath rather than guessed
  at: an untagged charge takes kilometres out of nothing and puts euros nowhere, so the figure can
  only ever come out **low**, and the card says how many are missing.

  **Written by @michapr.** He built the range-extender half on his own fork on 30 July and never
  offered it as a pull request. His version priced the *consumption*; Silvio's call was that a cost
  is the whole cost, so this one divides money by distance instead. Then he asked for the same card
  on a plain BEV, which is why it is one function serving both cars.

- **The Statistics page says how far back it goes.** A line under the title: *data recorded by Mate
  since &lt;date&gt; — not the car's own total*. Not one figure on that page is the car's lifetime
  counter, and the page never shows that counter anywhere: on a car reading 4803 km with 1877
  recorded, a card headed **Total distance** invites exactly the wrong reading. Said once, for the
  whole page, instead of defending each card from the same misunderstanding.

### Fixed
- 🔴 **A merged trip's petrol vanished from the period card.** Joining two trips writes
  `merged_into_id` and **nothing else** — the child keeps the tank readings it was recorded with.
  `get_fuel_totals_between`, behind *Energy by date range*, was the only fuel total in Mate
  filtering those children out, while the distance beside it never did. So the litres came up short
  and the kilometres did not, and the L/100 km divided one by the other.

  On **@michapr**'s B10 (beta [#23](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/23)) his 28 July 07:56 trip was merged into the 2 km hop before it and
  carried **3.7 of his 9.6 L**: the card read **5.9**, through four rounds of me looking in the
  wrong place. It is the second half of his #20 — on 31 July I fixed how a merged group *reads* its
  fuel and left the period query filtering the child away. One rule, two places, one corrected.

- **A range-extender's two "per 100 km" figures divided by different distances.** In the Trips
  header the consumption tile is the efficiency *measured by the car*, and Mate stores none for a
  generator trip — so it covered only the battery-driven part of the window while the litres beside
  it covered all of it. On a range-extender that tile now uses the same kilometres as the fuel tile
  next to it; a BEV keeps the measured figure, and so does the Statistics page, where it is labelled
  with the distance it covers. Reported by **@michapr** (beta [#24](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/24)).

  Side effect worth knowing: on a car driven mostly by the generator that tile used to read **—**,
  because none of those trips has an efficiency to average.

## [3.6.9] — 2026-08-05

### Fixed
- 🔴 **The charger's-own-kWh pencil disappeared from the day drawer and the search results.**
  Introduced in v3.6.7: the flag that decides whether to offer the field was passed through the
  shared page context, and the charge card is **also** rendered by two partials that build their
  context by hand. It survived on the Charges page and vanished exactly where you actually look at
  a charge. **@ghuaywen-ai**, who asked for the field, watched it go between v3.6.6 and v3.6.8.

  It is a template global now, not a route variable — no route can forget it, including one written
  tomorrow.

- **A range-extender's fuel was missing from the day header on the Trips page.** The month strip and
  the page header got it in v3.6.8; a day's own line was the third place those trips are added up,
  and it already carried the litres — they simply were not printed. Reported for the third time by
  **@michapr** (BetaTester #11), which is twice more than should have been needed.

- **The manuals promised a Log out that is not always there.** All four said the menu holds
  **⚙️ Settings** and **🚪 Log out**, without saying the second one appears *only when an access
  password is set* — and that it ends the password session, not your Leapmotor account. Unlinking
  the account is a different button, in **Settings → Vehicle**. **@JoseRMorales** (#223) went
  looking for the first and wanted the second. Both are now described, and distinguished.

### Changed
- **The average consumption says why, not just how much.** On a range-extender the note under it now
  reads *"over battery-only kilometres: 273 km"* — @michapr's own wording, because a figure that
  explains itself is remembered. On a full-electric car the missing kilometres are simply trips with
  no consumption figure, so the plain wording stays there: one label true in both places does not
  exist.

## [3.6.8] — 2026-08-04

### Fixed
- 🔴 **A trip Mate had no energy figure for was counted as having used none.** *Energy used* on the
  Statistics page summed `distance × COALESCE(efficiency, 0)`, and on a range-extender Mate blanks
  the efficiency of every trip the generator ran — deliberately, because a battery percentage stops
  measuring how efficiently the pack drove you once an engine is refilling it underneath. So those
  trips contributed **zero kWh**, not "unknown". **@michapr** (BetaTester #24) read 37.85 kWh where
  his own `SUM(ec_kwh)` said 41.6 and could not trace the gap to anything on screen.

  Such a trip is now left out of the total rather than counted as nothing, and where the car
  measured its own energy that figure is used. The tile also says how many trips it speaks for when
  some are missing — gated on the kilometres actually missing, so a stray zero-kilometre trip never
  earns the note.

  The trip's own efficiency still comes first, and the car's figure is only the fallback: preferring
  it would have quietly overruled the *"use the car's energy"* setting — measured on a real
  full-electric car, the total moved 338.75 → 349.21 kWh for nobody's benefit.

### Added
- **The average consumption says what it is averaged over.** The same reader saw **13.9 kWh/100km**
  at the top of Statistics and **9.6** in the card below and could not tell why. Both were right:
  13.9 covered the 272 of his 434 km that have a consumption figure — the battery-only ones — and
  9.6 was the car's own measurement over all of them. The tile now carries its kilometres, and the
  full explanation on hover. On a car where they are the same, nothing is shown.

- **The petrol side of the Trips page** (**@michapr**, BetaTester #11, asked twice). Every trip had
  its litres and nothing did where those trips are added up: the month strip above the calendar now
  carries the fuel beside the electric figure, and the page header gains a **litres burned** tile in
  the slot where a range-extender's regen tile is already hidden. Litres per 100 km over all the
  kilometres, the same basis the car's own display uses. Silent on a month driven purely on the
  battery, and absent entirely on a full-electric car.

## [3.6.7] — 2026-08-04

### Fixed
- 🔴 **The Charges page and every trip detail could answer with an error.** Introduced by v3.6.6
  itself: that release added a `gross_kwh` column and five queries that name it, but the migration
  which creates it lives in the poller, and the web never runs one. Between an update and the
  poller's next start — and permanently on an install whose poller is not running — those pages
  raised a database error instead of rendering. Found on a real instance the same evening.

  Two fixes, not one. Every query now asks the schema before naming the column, so it degrades to
  the previous answer (identical, since a database without the column holds no typed figures). And
  **the web now brings the schema up itself at startup**, which is the part that stops the next
  column doing this again: a reader that depends on a schema should guarantee it rather than hope.
  Only the schema — the poller keeps its eleven data repairs, which belong to the process that owns
  the data.

- 🔴 **A range-extender's fuel totals still used the old rule.** v3.6.6 corrected the litres of a
  single trip and left the two aggregates filtering their rows on the coarse tank-percentage signal,
  so a trip below that floor never reached the corrected reader at all. The trips list was right and
  every total was not: **@michapr** (BetaTester #23) updated, and his all-time figure stayed at
  5.9 L against **9.64 measured off his own signals** — 39 % missing. Both totals now admit a trip on
  either signal, and the summary goes through the same reader as everything else instead of working
  the litres out a third time.

- **MQTT settings did nothing until the bridge was restarted.** The broker, port, credentials, TLS,
  discovery and topic prefix were read once, when the bridge was created, and never again — while
  the page answered *"Saved — restart not needed"* in all seven languages. It now notices a changed
  configuration and reconnects on the next cycle.

- **A command could be executed for a car that is not yours.** The command topics are wildcards, and
  the VIN in the topic went straight to the cloud API. Two installs sharing a topic prefix therefore
  each ran the other's commands. Refused now, with a line in the log saying why.

- **On the Charges page, the AC vs DC card did not add up to the total beside it.** It summed the
  energy that reached the battery while *Total energy* summed what was billed — 19.4 kWh apart on
  the test data, and older than everything else here.

### Added
- **Mate notices a second Mate on the same MQTT topic prefix.** Two installs sharing one prefix are
  a single device to Home Assistant — the discovery id is built from the prefix and the car — so the
  second one appears to do nothing at all, while in fact **every command runs twice**, from two
  accounts, on the same car (@ebagnoli, BetaTester #13). Each install now announces itself on the
  broker, and when it hears another: the **BetaTester build moves itself** to `<prefix>_beta` and
  says so in Settings; the official install never moves, because its entities are the ones your
  automations point at. Two official installs are told, and left alone — neither can claim to be the
  real one.

- **The topic prefix field says what it is for.** It is the box that separates two installs, and it
  carried no explanation at all.

## [3.6.6] — 2026-08-04

### Fixed
- **REEV: a trip's litres were counted far too low.** Reported the same day by **@michapr**
  (BetaTester #23) and **@pdifeo** (BetaTester #22), pointing at the same thing from different
  cars — 9.64 L of a real refuel reported as 5.9, and about 2.1 L over 35 km reported as 0.3.
  Measured on their own diagnostics bundles: the car sends **two** fuel figures, the tank
  percentage (signal 3235, one step = 0.1 % = about 50 mL) and its own litre counter (signal 3263,
  one step = 1 mL). Mate applied a 0.2 % floor to the coarse one and returned **before** ever
  reading the fine one, so everything a percentage step could not resolve was thrown away. Each
  signal now gets a floor of its own size, and the litre counter is preferred wherever the trip
  carries it.

- ⚠️ **REEV: fuel consumption is now measured over the whole trip, like the official app.** It used
  to divide the litres by the kilometres the generator ran, which answers a different question and
  gave figures no owner could compare with their car's own display: **a trip that read 15.9 L/100 km
  reads 2.2 now**. The numbers you have already seen will move — nothing was recorded wrongly, the
  denominator changed.

- **On the Charges page, two totals on one screen did not add up.** The **AC vs DC** card summed the
  energy that reached the battery while **Total energy** right beside it summed what was billed —
  19.4 kWh apart on the test data, and older than everything else in this release. Both now use the
  same rule.

- **Three internal queries asked for too few columns.** They read the billing rule but did not
  select the column it needs, so instead of failing they slid quietly to the battery figure: a
  wrong total that looks perfectly plausible. A test now scans for that shape.

### Added
- **The charger's own kWh, typed in** — requested by **@ghuaywen-ai** (#222). On a public charger
  Mate has no meter: it reads what entered the battery, while the charger bills what left its own.
  You can now type that figure on the charge card. It **opens only on purpose and is always empty** —
  never pre-filled with the previous value nor with the measured energy — so a stray click cannot
  change it and pressing OK on an empty box leaves everything as it was, including the price of an
  old charge. *Remove* takes a wrong number back. From there it prices the charge exactly as a
  wallbox counter does at home, and shows how much the on-board charger turned into heat.

- **The month above the charge calendar says both sides**, in words: *"154.93 kWh delivered ·
  142.57 in battery"*. The bare number it replaced was already a mixture — the wallbox counter where
  there was one, the battery figure everywhere else — with no word to say which.

- **The average price says what it divides by.** On the Charges page and the Monthly report it now
  carries *"on the billed kWh"*, with the full explanation on hover. Reported by Silvio, who had the
  Overview showing 0.271 €/kWh and the Charges page 0.250 for the same history: both right, and the
  whole difference is the conversion loss — one is per kWh you paid for, the other per kWh that
  reached the battery, which is what a trip consumes.

### Changed
- ⚠️ **A figure you type is now part of the energy Mate reports.** The charger's own kWh was kept out
  of the totals when it shipped, so that a typo could not inflate them. It is in now, because the
  calendar had begun saying "delivered" with it while the totals beside it ignored it, and two
  totals under two words that mean the same thing are worse than one total that can be mistyped.
  What the car measured into its battery is never overwritten, and the price a trip is costed at
  still divides by that measured energy.

## [3.6.5] — 2026-08-04

### Fixed
- **A notice could stand between you and the menu.** Reported by **@ebagnoli** (BetaTester #13),
  reading Mate on a phone: *"ci sono voci del menù alle quali non puoi accedere"*. The four sticky
  strips at the top of every page — the BetaTester banner, the demo bar, the auth warning, the
  MateDesktop notice — all carried a z-index **above** the off-canvas drawer, so opening the menu
  painted the strip over its top entries. A tie counts too: at equal z-index the later element in
  the document wins, and every strip comes after the sidebar. All four now sit below it.

- **"Got it" did not put the notice away.** Found while fixing the above. htmx performs **no swap**
  on a `204 No Content` by design, so `hx-swap="delete"` never ran: the request went out, was
  answered, and the strip stayed until the next page load. The MateDesktop notice had carried that
  since it shipped, with a comment claiming the opposite. Both dismissals now answer 200.

- **The page reloaded while you were still choosing.** **@pdifeo** (BetaTester #22) sat in merge
  mode weighing which trips to join: *"se si pensa troppo la pagina fa refresh resettando la
  visualizzazione e bisogna ripensare di nuovo"*. Every page refreshes itself every 30 s, and its
  guards knew about a focused text field but not about an unfinished choice — thinking is not
  idling. Merge mode and an open dialog now hold the reload off.

- **Battery health could read above 100 %, and the number that explains it was invisible.**
  Reported by **@danielvilhena** (#221) with the capacity set to 67.1 kWh and health measured
  against 65. Three things were wrong around one setting: the form carried its own default (67.1)
  while the code read another (65.0), so what you saw was not what your energy was computed with;
  the SoH reference — snapshotted once, deliberately, so that adopting an already-aged measurement
  cannot reset health to ~100 % and hide the ageing — was **frozen for ever** with no way to see or
  correct it; and it was **absent from the diagnostics**, so a bundle could not answer the question
  it was sent to answer. One default now, the reference is reported and editable, and the four
  manuals say what it is.

### Added
- **The BetaTester banner can be closed, and the build stays identifiable.** A tester runs two
  installs side by side, so the warning is now dismissible while a small red **BETA** badge sits
  permanently beside the version, in both sidebars. Red because the DEMO badge next to it is amber
  and the two must never be read as one.

- **The monthly report stops being electric-only on a range-extender.** It had exactly one REEV
  branch — it *hid* the regen tile — and added nothing, so an owner who spent half the month on the
  generator got a report about the other half. Now: litres burned, L/100 km over the generator-on
  distance, litres refuelled and what they cost. Burned and bought are kept apart on purpose: a
  tank is filled on one day and burned over the following fortnight, and adding them would answer
  neither question.

- **Both energies over the period you chose.** Asked for by **@michapr** (BetaTester #11). The
  period card answered the electric half for any window; the petrol half now comes from the trips
  in that same window — and the litres are the car's **own** counter (signal 3263) wherever the
  trip has it, not tank-% × an assumed capacity.

- **A card with the car's own two consumptions.** `getPlugInLastNweeks100kmEC` returns kWh/100 km
  **and** L/100 km measured by the car, and its figures match the official app's screen to the
  decimal — confirmed against @pdifeo's C10 bundle (11.1 kWh + 1.6 L) and our own B10 (20.0 + 0.0).
  It takes no date range, so the card says which window it is showing: leaving that unsaid beside
  the monthly tiles is how two correct numbers start looking like a contradiction.

### Changed
- **The consumption arrow now compares what the tile shows.** The monthly report's average comes
  from the car's own metering over the month; the little vs-previous-month delta beside it was
  computed from a different quantity, and on a range-extender from the electric part of each month
  alone. Both months are now weighed on the same measured basis.

## [3.6.4] — 2026-08-03

### Added
- **A `Ready` entity on MQTT — the moment an automation can still act.** Asked for by
  **@Torbynator** (#220), who wanted to open the sunshade when the car wakes up and found that
  neither published state fits: `state` only turns to *driving* once a gear is engaged or the car
  moves, and by then the car refuses the very commands the automation wants to send; a door opening
  is the other end — it fires early, and it fires without a drive following.

  Mate has been reading that signal since the power-on automation shipped, and storing it, and
  never publishing it. It is now a Home Assistant **binary sensor** (`device_class: running`), so
  it arrives as an on/off entity with an edge to trigger on rather than a string to parse. No new
  signal, no extra cloud call — the figure was already in hand.

### Fixed
- **A range-extender trip showed one energy where there are two.** Reported by **@michapr**
  (BetaTester #11): *"only one will be shown"*. In the trips list the electric figure and the fuel
  figure lived behind two conditions that can never both be true — a trip where the generator ran
  has its efficiency blanked on purpose (a SoC drop stops measuring anything once the generator is
  refilling the pack underneath), so the row had nothing electric to print and showed petrol alone.

  The electric number existed all along and the trip's own page has been showing it; the list
  simply never asked for it. It does now — the same call, reading a stored column, so a list of
  twenty trips costs twenty dictionary lookups and not twenty cloud calls.

  **Where it is missing, the row says so.** A range-extender always draws from the pack to move, so
  a row carrying litres alone reads as a car that ran on petrol only, which never happens. The
  metered figure comes from the cloud and can lag; that slot now holds a dash naming the reason,
  because the existing *provisional* marker expires after six hours and the absence then went
  silent — with no way to tell *not yet* from *never*.

## [3.6.3] — 2026-08-03

### Fixed
- **A wallbox meter that stops mid-charge no longer bills you for only the kilowatt-hours it managed
  to count.** Opened by **@riri19** (#215) as a physical impossibility — 22.1 kWh from the wall for
  33.8 kWh into the battery, which no charger can do — and then solved by **@riri19 himself**: his
  Tuya energy sensor froze at `2083.40 kWh` for **2h18** while the car went on charging at 6.9 kW,
  and only caught up **24 hours later**, by which time that charge had been closed for a day. The
  session was billed on **22.1 kWh instead of 38.9** — undercharged by roughly 43 %.

  Mate already guarded the opposite failure, a counter that runs away (a lifetime total, a glitch),
  because a rise that is too big announces itself. A rise that is too small does not: it just looks
  like a slower charge.

  The new check is between two **measurements**. While the counter does not move, Mate adds up the
  energy the **car itself** reports drawing; once that passes 3 kWh, no meter's resolution can
  explain the silence, so the counter's total for that session is dropped and the charge is billed
  on the energy that reached the battery — short by conversion losses, but not short by an unknown
  amount.

  It is deliberately **not** a comparison against that battery figure. That number is ΔSoC × the
  capacity configured in Settings — an estimate resting on a constant anyone can type in wrong — so
  letting it sit in judgement would throw away a perfectly good meter on every single charge for
  those owners. The weaker number must not get to discredit the stronger one.

  Two things keep it from firing on a healthy meter: it only counts while the car reports at least
  1 kW, so a counter standing still because nothing is flowing proves nothing, and it is counted in
  kilowatt-hours rather than in polls, so a coarse meter that legitimately ticks once per kWh is
  left alone. Once it does trip it **latches** — energy missed while the meter was frozen stays
  missed even if the meter later comes back to life, which is precisely what @riri19's did, a day
  late and into the wrong session.

### Documentation
- **The four user manuals now describe this protection beside the one it mirrors.** *"I see a strange
  charge / an absurd cost"* already explained the runaway counter; it now covers the stopped one too,
  in English, Italian, French and German.

### Internal
- **A test that had never once run.** Two tests in `test_reev_total_consumption.py` were both called
  `test_it_is_scoped_to_the_current_vehicle` — one for the derived total, one for the actual spend.
  Python keeps the last definition, so pytest collected 14 tests out of 15 written and the trip-side
  vehicle-scoping check was silently discarded. Both renamed after the function each one covers, and
  the restored test was watched fail before being trusted.

- **Six return annotations that told a type checker the wrong thing**, with no runtime change.
  `finalize_trip` was declared `-> None` while returning a distance that `trip_distance_km` leaves
  `None` when neither the odometer nor the GPS track can supply one — the caller already handled
  that, only the signature lied. The other five return `cur.lastrowid` straight after an `INSERT`,
  which the sqlite3 stub types `int | None`; widening them would have pushed a check that can never
  fire onto every caller, so the invariant is written down at the source instead.

## [3.6.2] — 2026-08-03

### Fixed
- **A charge that cost nothing was read as a charge with no price, and the battery kept the rate of
  the last one you paid for.** Reported by **@oenukr** (#218), who charges from his own solar panels
  and marks those charges 🆓 Free: *"if I only paid 50 euros to charge my car, the total cost of my
  trips cannot be higher than that."*

  He was right, and the gap was not small. `cost = 0.00` is only ever written on purpose — the Free
  mark, the FREE type, a time band priced at 0, a manual 0 — while a price that is genuinely unknown
  is stored as NULL. One guard treated the two the same and dropped both, so free energy entered the
  pack and the blended €/kWh did not move. Measured on the real code: **50 kWh from the roof plus
  10 kWh at €0.30 — €3.00 spent — used to bill €18.00 across the trips that emptied that pack. It
  now bills €3.00**, to the cent. The error had no ceiling: the rarer your paid charges, the further
  it ran (one paid charge in twenty billed twenty times what you spent, and grew with every trip).

  The two figures @oenukr compared now agree in the case he described — 30 kWh free + 30 kWh at
  €0.30 gives **0.150 €/kWh** on both the Charges page and the battery card, which is €9.00 ÷ 60 kWh.
  They still differ once you have driven in between, and correctly so: the Charges page averages a
  **period**, the battery card prices **what is in the pack right now**.

  Trips made before a paid charge are untouched, for ever — the rate a trip is priced at only ever
  looks at charges that ended **before that trip started**, and costs are recomputed from history on
  every page rather than stored. That is now held by a test.

- **A trip that ran entirely on free energy said "—" where it should have said €0.00.** With every
  charge free the blended rate is exactly zero, and three places tested it for truthiness rather
  than for "no value" — so the trip cost, the rate beneath it and the battery card all went blank.
  A dash means *we do not know*, to the one owner who knows for certain. A charge that has no price
  yet still shows "—", which is what it is for.

### Changed
- **A refuel that fills the tank to the brim is now shown as a floor, not an estimate.** Found by
  **@pdifeo** (BetaTester #21), whose pump delivered **10.51 L** where the car reported **9.204 L**.
  It is not arithmetic: the car's own litre counter stops at its nominal full tank — 100.0 % and
  47 500 mL arrive in the same frame — while the tank keeps accepting fuel. Across 22 459 readings
  the value never once exceeds that. Such a refuel now reads **"≥ 9.2 L"** instead of "≈", with a
  line in seven languages telling you to take the litres off your receipt. Partial refuels are
  untouched: there the car's counter is exact.

- **Sixty-two German strings: `Ladung` → `Ladevorgang`.** *Ladung* is the everyday word; the German
  regulator (Bundesnetzagentur, *Ladesäulenverordnung*) calls the process a *Ladevorgang*, which is
  also what the app already used in nineteen other places. Classified one by one rather than
  replaced, because the word has two meanings and one of them was already right: the vampire-drain
  subtitle keeps *Ladung*, the electric charge that leaks away while the car is off. The gender
  changes with the word, so the sentences around it changed too — *Letzte Ladung* → *Letzter
  Ladevorgang*, *während dieser Ladung* → *während dieses Ladevorgangs*, and *sie* → *er* where a
  pronoun pointed at it. The sidebar now reads **Ladevorgänge / Tankvorgänge**, a pair.

### Documentation
- **The German user manual follows the app**, including the table of contents: it still sent readers
  to a menu entry called *Ladungen*, which no longer exists. Thirty-eight lines, with the same
  two-meanings rule — the four places where *Ladung* is the battery's charge are deliberately left.

### Internal
- **A code comment claimed a measurement that was really a ceiling.** @pdifeo's earlier full tank
  (BetaTester #17) read 33.390 → 47.500 L, and that 47.500 was written down as confirmation of the
  C10's tank size on a second car. It was the counter's limit, and in the same report he had already
  said he stopped at the pump's first click with room to spare. The constant itself stands — it also
  comes from dividing signal 3263 by 3235 across seven bundles — but it is what the **car** calls a
  full tank, not what the tank holds: his measured at least 48.81 L. Corrected in all three places
  that repeated it, including a test's own docstring.

## [3.6.1] — 2026-08-02

### Changed
- **The blended €/kWh under a trip's cost now says why it is above the tariff you typed.** Asked for
  by **@michapr** (#207), who wanted a trip priced as `8 kWh × €0.25 + €0.07 charge loss`, or as a
  single rate with a note explaining it.

  The single rate already existed: the trip detail has printed the €/kWh each cost was worked out at
  since #200, with a tooltip describing how it blends. What the tooltip never said is **why** it
  comes out higher than the price in *Charge Prices* — on a home charge billed on a meter's kWh, the
  rate is the tariff divided by the charger's efficiency, so the energy that became heat before
  reaching the battery is already in every trip's cost. Someone reading the two numbers side by side
  had no way to learn that the gap was the charger. One sentence, seven languages, no number moved.

  The split form he also proposed cannot be built: without a meter there is nothing measuring the
  loss, so the €0.07 would have to be a figure the user guesses. On that instance the rate correctly
  equals the tariff — numerator and denominator are the same energy — and it starts telling the
  truth on its own the day a metering socket is mapped.

- **Twelve more strings, checked the same way as the forty-one before them.** Polish put the side
  before the position on doors and windows — `Lewy przedni`, not `Przedni lewy` — which is how the
  tyre rows in the same file were already written. Its odometer said `Licznik`, a meter of any kind;
  the Highway Code (*Prawo o ruchu drogowym*, art. 80a) calls the instrument `drogomierz` and every
  dashboard calls the reading **`Przebieg`**. French battery health was the only telegraphic entry in
  a menu of full noun phrases, and is now *Santé de la batterie*.

### Fixed
- **A code comment described a diagnosis that had been publicly retracted ten days earlier.** The
  REEV charge-detection rule in `poller/client.py` cited beta [#12](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/12) (@michapr) as an instance of "pack
  current reads ~0 during AC charging". That is the **C10** signature; his B10 reads −3.8 A, the
  rule shipped for him in v2.8.4 changed nothing, and his car is covered by the SoC-rise branch
  added in v2.8.6 instead. All of that was said in the issue on 24 July and never made it back into
  the file. Two REEV models, two signatures, two rules — now written down where the next reader
  will be.

### Internal
- **A new import here can no longer quietly break MateDesktop.** The desktop app is a signed shell
  plus a payload, and the payload is this repository's `web/` and `poller/`, delivered on every tag.
  The shell only carries what it was built knowing about, and its updater's dependency guard reads
  `requirements.txt` — so it catches a new *package* and is blind to a new module of the standard
  library. Mate 3.4.10 started importing PIL directly and the contract never learned; it worked only
  because Pillow arrives as an extra of `leapmotor-api[image]`. A test now re-runs the scan and goes
  red before a tag rather than on someone's Mac. It skips where the two repositories are not side by
  side, which is everywhere except the machine releases are cut on.

## [3.6.0] — 2026-08-02

### Added
- **Mate goes on your phone's home screen, as an app rather than a bookmark.** Asked for by
  **@jose-knowee** (#213), who wanted a native Android/iOS app. There can't be one — Mate is a
  recorder that has to poll for years, and a phone suspends what runs in the background — but the
  part he actually wanted is the launch, and that costs a manifest and six tags.

  *Add to Home Screen* now gives Mate **its own icon** and opens it **full screen**: no address bar,
  no toolbar, about **110 px of screen** handed back. Not one line of the interface changed — it was
  already responsive. Only the window around it.

  Two icons ship for it, drawn from the existing `mate-icon.svg`: a square 180 px one for iOS, which
  ignores the manifest and rounds the corners itself, and a 512 px maskable one for Android, which
  crops to a circle.

  Every path is relative on purpose. Under the Home Assistant add-on, Mate is served below an ingress
  prefix, and a leading `/` would send the installed app to the host root — breaking it for add-on
  users only, on a screen nobody opens twice. A test scans for that.

  It remains a shortcut to the server you run: with that off, the icon opens nothing.

### Changed
- **The translations were checked against the sources that define the words**, rather than against
  the ear of whoever wrote them — which was, for four of the seven languages, this project's
  maintainer working from English. 41 strings moved.

  **German — one concept had three names.** `Sitzung`, `Ladesitzung` and `Ladevorgang` all meant a
  charging session. The **Bundesnetzagentur**, the federal regulator, uses **`Ladevorgang`**, and
  neither `Sitzung` nor `Ladesitzung` appears anywhere in its material; the *Ladesäulenverordnung*
  defines `Ladepunkt` and `Ladesäule` in the same register. 30 strings now use one word, declined
  properly — `diesen Ladevorgang`, `jedes Ladevorgangs`, `Ladevorgängen`, and in one sentence the
  pronoun had to follow the gender. Three of those strings were **not** charging sessions and were
  handled separately: the car's power-on window is now `Einschaltphase`, and the drives the car
  fails to upload are `Fahrten`.

  **Portuguese — `Estação` was not the word.** The national charging network **MOBI.E** and the
  regulator **ERSE** both say **`Posto de carregamento`** (their own tiers are PCN, PCR, PCSR,
  PCUR). Five strings changed, with the gender agreements that follow from *posto* being masculine.

  **Two words were simply wrong, and the files said so themselves.** German `clim_temp` read
  *Temperatura* — an Italian word — two keys away from `sched_temp`, which already had `Temperatur`.
  German and Polish `mode_comfort` read *Comfort* while `comfort_section` already had `Komfort`.

  **And three were merely inconsistent**, with no source to settle them, so they follow the majority
  already in the file: Italian and French now say *connesso* / *connecté* for a plugged-in cable in
  all three places, and Dutch writes `Kilometerstand` in full, as it already did twice out of three.

  Confirmed correct and left alone: Portuguese **autonomia combinada** — which is
  [Leapmotor Portugal's own wording](https://www.leapmotor.net/pt/c10reev) — plus *tomada*,
  *bagageira* and *odómetro*; Polish **rekuperacja**, **zasięg**, **stacja ładowania**; Dutch
  **actieradius**, and *accu* / *batterij*, which RVO and Milieu Centraal use interchangeably;
  French **bornes de recharge** and **autonomie**.

### Documentation
- The four user manuals explain how to put Mate on a phone's home screen, and why it is a shortcut
  rather than an app. The README's Features list gained it in both languages.

## [3.5.2] — 2026-08-02

### Changed
- **The access password is typed twice.** Raised by **@rop12770** (#214), who set one in
  Settings → Access, gets in from his PC, and is told it's wrong on his phone.

  With a single box a typo is hashed in silence — and the machine you set it on **keeps working**,
  because the browser saved what you actually typed. The mistake only surfaces on the next device,
  by which time there is no way left to know which key you pressed. The password isn't readable
  anywhere afterwards, by design: what's stored is a salted hash.

  Both forms now ask for it twice — the one that turns protection on and the one that changes it —
  and refuse to save unless the two agree. The check runs in the browser through its own constraint
  validation, so an htmx form simply doesn't fire while they differ, **and** on the server, because
  a form attribute is a convenience rather than a guarantee.

  Each box also has its own **👁 reveal button**, like the setup wizard has always had. One per box
  rather than one for both: the pair exists so you can compare them, and uncovering only the one you
  doubt is the smaller exposure. It re-hides itself as soon as you leave the field, so a revealed
  password can't be left sitting on a shared screen.

- **And the card now says how to get back in.** Losing it does **not** lock you out for good, but
  nothing said so anywhere: the *New password* box never asks for the old one, so from any device
  still signed in you can just set a new one, and `MATE_AUTH_PASSWORD` overrides whatever is stored
  if no device is. That is now written where you set the password, and in all four manuals.

### Documentation
- The four user manuals gained the **🔐 Access** card, which none of them described: what it
  protects, why it is typed twice, and the two ways back in. Their version stamps are current again.

### Internal
- **The template guards for this card live where CI can run them.** The endpoint tests import
  `web/main.py` and therefore FastAPI, which CI doesn't install, so that whole file skips there —
  and a guard that only runs on one laptop guards nothing. The checks that merely read
  `settings.html` were split into their own file with no such import. The same mistake cost a
  release the day before; this time it was caught by running the suite the way CI runs it, before
  publishing rather than after.
- One of those guards exists because the reveal button was first written with its two icons inside
  `data-` attributes. An `<svg>` in an attribute ends the attribute on its own first quote: the
  browser then parses the rest of the tag as text and the **entire card renders as an empty box** —
  no console error, no failing test, nothing in the logs. Jinja's `|e` doesn't help, since a macro's
  output is already Markup. It was found by looking at the page; the guard now scans every template
  for markup inside an attribute value.

## [3.5.1] — 2026-08-02

### Fixed
- **The test suite runs again on CI.** v3.5.0's new tests for the cloud-total guard imported
  `web/main.py`, which pulls in FastAPI — absent from the minimal environment CI installs, so the
  run died at collection. Nothing shipped to anyone was affected: the Docker images were built from
  the same commit and the app itself never imports differently.

  The guard has moved from `main.py` to `db_reader.py`, beside `get_trip_totals_between`, which is
  where the data it compares already comes from. The other FastAPI-dependent tests guard themselves
  with `importorskip`, which would have **skipped these exactly where they matter most**; moving the
  function lets them run everywhere instead. Verified with FastAPI deliberately blocked.

## [3.5.0] — 2026-08-02

### Changed
- **Numbers are written the way your language writes them.** Until now `money` and the €/kWh price
  followed the interface language and nothing else did, so an Italian Monthly Report showed a cost of
  **38,74 €** on the same row as an energy of **110.3 kWh** and a price of **0,250 €/kWh** — the same
  page writing the same kind of number three different ways. One rule now sits under every displayed
  figure: the decimal separator is the comma in Italian, French, German, Dutch, Polish and Portuguese,
  and stays the dot in English.

  This is the most visible change in the release: it touches every screen. Nothing is rounded
  differently and no value moves — only the mark between the units and the decimals.

  Three places deliberately keep the dot, and it is worth knowing why: the width of a coloured bar,
  anything inside a chart, and the three live readouts beside the Advanced-settings sliders, which
  JavaScript rewrites from the slider itself the moment you touch it. A comma in the first two is a
  syntax error rather than a separator — the bar would silently collapse to nothing — and in the
  third it would flip to a dot under your finger.

### Fixed
- **The Monthly Report opens on the month you are in.** It used to open on the newest month that had
  any data, so on the 2nd of August, with nothing driven yet, it showed **July** — July's real
  numbers, under this month's page, with only the small header saying so. The current month now
  always exists: empty, it says *"nothing this month yet"* instead of a page of zeros, and it shows
  no comparison against last month, because every tile would read −100 % and that describes the
  calendar rather than the driving.
- **The report stops trusting a monthly total that is missing drives.** Reported by **@riri19**
  (#212), whose Trips page and Monthly Report disagreed by a quarter about the same three drives:
  16.4 kWh/100 km against 12.3, over the same 221 km.

  Neither figure was miscalculated — they come from **different places**. A trip takes the car's
  official energy when the cloud has a usable one and falls back to the battery-percentage estimate
  when it doesn't; the report's two tiles took the car's official **monthly** total whole, with no
  fallback and no check.

  That total is only as complete as the car's uplink. On his own log the car was reading a stale
  cloud frame on **59 %** of its polls while driving — and, on the afternoon in question, **8.9 %**
  of the time during the drive whose energy the cloud got right against **75.1 %** during the one it
  lost entirely. The drive the cloud missed is the drive the car couldn't reach it.

  So the same reasoning the per-trip path already used now applies to the period: when the car's
  total comes back far below what Mate's own trips add up to for the same window, Mate shows **its
  own figure** and says so under the tile. The Guida / A·C / Altro split stays the car's own — that
  breakdown exists nowhere else — with a line noting it covers only what reached the cloud. The
  threshold was measured rather than picked: three months of a well-connected car put the cloud at
  0.895, 1.032 and 0.982 of the local sum, and his broken month at 0.747. Only the low side is
  guarded; a total **above** the local sum is normal, since it carries climate and standby energy
  that no trip is charged with.

### Documentation
- The four user manuals and the README's Features list describe both report changes: where *Average
  consumption* and *Energy used* come from, and when Mate overrules the car's own total.

## [3.4.10] — 2026-08-02

### Fixed
- **The charging animation on the Overview now runs to the end — and works out for itself which way
  round it goes.** Reported by **@banolka** (#211), who noticed on a T03 that the energy appears to
  flow *out of* the car rather than into it, in the official app as well as here.

  The animated car picture is not drawn by Mate: it comes from the cloud as a set of numbered frames,
  per vehicle, and both apps simply play them in file order. Two things were wrong with the playing.

  First, **not all the frames were being shown**. The player asked for a fixed range that stops at
  frame 15; the B10's package ships **18**, and the three it never drew are the ones where the pulse
  completes its run and reaches the car. So the pulse died halfway along the cable and restarted at
  the wallbox.

  Second, **the direction was being assumed**. It is now **measured from the package itself**: the
  highlight is the only thing that moves between frames, so subtracting the darkest value each pixel
  ever takes leaves the highlight alone, and whichever end of its path is nearer the car's silhouette
  is the end the energy should arrive at. If the first frame is already there, the package is
  numbered backwards and it is played in reverse. On the B10 the measurement separates the two ends
  six-fold (61 px against 10) and confirms the existing order, so nothing changes for a car that was
  already right.

  Measuring it rather than writing "reverse it for the T03" in the code matters for two reasons: it
  works for models nobody here owns, and it cannot go stale — a hardcoded reversal would start
  breaking the animation the day Leapmotor corrects the artwork, and nobody would connect the two.

### Changed
- **"Home" no longer reads as "wallbox".** The label under the Home charge type said *Home Wallbox /
  private charging*, and the slash made the two look like the same thing. **@michapr** (#207) built a
  whole energy calculation on the assumption that a home charge is always metered by a wallbox, and
  **@adoewa** (#196) asked for a feature that already exists for exactly that reason. It now reads
  **Wallbox or domestic socket**, in all seven languages.

  *Home* is where you charged, not what you charged from. That distinction decides which energy you
  are billed on: with a wallbox meter mapped, the meter's kWh; without one, the kWh that reached the
  battery, exactly like a public charge.

### Documentation
- **The four user manuals said the same thing the label did** — "Home (your wallbox)". All four now
  explain that Home is a place, not a device, and which of the two energies gets billed in each case,
  with the charger's 10–15 % heat loss named as what sits between them.
- **`CAPABILITY-PROFILE.md` describes the code again.** It had been left at v1.11.5 since June and
  had drifted into being wrong in three ways: it documented **one** gating mechanism when there are
  now **three** (the per-VIN verdicts it describes, plus `MODEL_ABSENT` for hardware a model doesn't
  have — #144 — and the ability whitelist for what the car itself declares — #142); it **contradicted
  itself about the windows**, saying in one section that the command works on a four-stop 0–10 scale
  and in another that it is accepted-but-not-executed; and it still listed the charge-port unlock as
  unconfirmed, when it has been confirmed actuating on a real B10 since June.

  It now also records the two things that were never written down: why the per-model table exists at
  all when the car declares its own abilities (because the T03's declarations are wrong in **both**
  directions — it claims heated seats it doesn't have and omits the A/C it does), and the T03 climate
  behaviour from #67, marked as what it is: derived from logs, not verified on a car we own.
- **Dutch: `inschakelbeurt` is gone.** It was a coinage no Dutch source knows, invented while
  translating "power-on session". The sentence now says the same thing with ordinary words. Every
  other `-beurt` word in the file (`laadbeurt`, `tankbeurt`) is genuine Dutch and untouched.
- Internal tidying that changes nothing on screen: the polling state machine's header listed a fixed
  interval per state (`PARKED_SLEEP 5 min`, `PARKED_ALERT 15s`) when the code has only **two**
  user-configurable cadences and maps every state onto one of them; and a 40-line charge-energy
  integrator that no longer had a single caller has been removed, with the three comments that
  pointed at it redirected to the function actually doing the work.

## [3.4.9] — 2026-08-01

### Added
- **Settings → Vehicle now names the account this instance signs in with.** Asked for by
  **@ebagnoli** (beta [#13](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/13)), who runs several Mate instances against several Leapmotor accounts and
  had no way to tell them apart from inside the app: the card gave the model and the VIN, and both
  of those describe the **car**, not the login. Two instances watching the same car were identical
  on screen. The row sits under the VIN and reuses the label the setup wizard already has, so it
  arrives translated in all seven languages rather than in English everywhere else.

  It is stacked rather than put on the right like Model and VIN, because that card lives in a
  ~280 px column and an address is longer than a VIN: on one line it broke mid-word
  (`silvio.bressa` / `ni@dxc.com`), which is unreadable at exactly the moment you are comparing two
  instances letter by letter. Shown in full, like the VIN beside it — the card is behind a menu on
  your own instance, and a masked address would not answer the question it exists to answer.

  Behind it, the "stored login first, environment second" rule now has **one** definition
  (`get_account_user`) instead of a copy in the command client — so the account the page names is
  by construction the account Mate logs in with, not a second guess at it.

### Documentation
- **The four user manuals are current again, and findable.** They still declared **v1.27/v1.28**
  with Mate at 3.4.8, and nothing in the repository linked to them. All four now carry the real
  version and describe what shipped since: the third install route (MateDesktop), the map's
  full-history routes with dashed bridges where the signal was lost, and the rewritten battery-health
  section — the 95 % cut-off, the pooling, and what the ± actually measures. English and Italian also
  gained the trip calendar, the day-scoped merge and the elevation lookup. README and DOCKERHUB now
  link them.
- The README's Features section gained the account row above, in English and Italian, and both lists
  were checked item-by-item — 40 entries each.

## [3.4.8] — 2026-08-01

### Changed
- **Battery health stops counting the BMS re-anchoring as energy.** **@riri19** has a nine-month-old
  B10 Max LFP reading **94.9 %** and said the figure was being dragged down by one charge with a
  small SoC rise. He was right, and the same charge sat in the maintainer's own history: a 12.9-point
  top-up ending at 100 % that estimated the pack at **57.7 kWh** where every other charge said 64-67.

  The cause is not the SoC being "noisy". An LFP's voltage curve is flat across the middle of its
  range, so the BMS counts coulombs and drifts; near the top the curve finally rises and it
  **re-anchors** — adding SoC points that no energy paid for. Dividing measured energy by a delta
  containing them under-states the pack, and worst on a short top-up where they are most of the
  delta.

  So the estimate now **stops at 95 %** instead of discarding those charges: a charge to 100 % is
  the one that re-calibrates the pack and belongs in the history, only its last few points leave the
  arithmetic. On the maintainer's 24-charge history every full charge rises — 64.5 → 66.9, 64.2 →
  65.6, 63.8 → 65.7 — while charges that never reach 95 % do not move at all, and the scatter across
  all of them falls from **2.39 to 0.67 kWh**.

- **The headline pools the charges instead of averaging their ratios.** It used to weight a charge
  by where it *ended*, so a 13-point top-up to 100 % counted as much as a 57-point charge. It now
  sums the energy and the SoC covered across a window measured in **SoC points** rather than in
  number of charges, so a charge counts in proportion to how much of the scale it actually spanned —
  riri19's own suggestion — and nothing is discarded to achieve it. The figure moves by **0.12 kWh**
  on a new charge where it used to move by 0.67.

- **The number no longer pretends to be a measurement.** Battery health now shows its own **scatter**
  next to it — *98.2 % ± 0.8* rather than a bare *93.6 %* — because the divisor is still a SoC the
  BMS counted, and removing a known systematic error makes the figure steadier, not true. A single
  charge shows no ±: printing *± 0.0* would be the false precision riri19 asked us to stop giving.
  _(#205.)_

## [3.4.7] — 2026-08-01

### Fixed
- **A charge left open no longer closes on the NEXT charge.** v3.4.4 taught the crash-recovery path
  to close an open session on the last reading taken *while charging* rather than on whatever the
  car was doing when Mate noticed. That search had no upper bound whenever no later charge row
  existed to stop it — so it walked forward through everything. **@mikeeeeekoo** updated Mate in
  the evening, the recovery ran, and his overnight charge closed on the **first sample of that
  evening's plug-in**: 17:10, 80.7 %, seventeen hours long, for a charge that had really ended at
  06:10 at 100 %. The previous behaviour was wrong too — it took the last position of any kind, a
  whole morning of driving — so this is a defect introduced by its own fix, and it found the one
  person who had reported the original.

  A charge now ends where its own **contiguous** run of charging samples ends: the first reading
  that says the car is not charging closes it, whatever happens hours later. A bound on continuity,
  not on time, because that is what "this session" means. His night replays as
  **00:06 → 06:10, 12.8 → 100 %, 61.0 kWh** — the figures his own app shows. A session still stuck
  open in your database is closed correctly the next time the poller starts. _(#208.)_

### Added
- **Refuels can write down where you filled up.** The 🧭 button that trips and charges already had
  is now on each refuel: it takes where the car was standing at that moment, turns it into a street
  address and puts it in the note — so the pump identifies itself instead of being typed in. Asked
  for by **@gm27271**: *"then users do not need to enter any notes, it will autodetect where the gas
  was added"*.

  One limit is built in deliberately. A refuel's timestamp is not always when the fuel went in: on
  one Mate spotted by itself it is when the **new level was first seen**, which for someone who
  fills up and drives home is the next time the car woke. So the address is only written when the
  car actually reported a position within twenty minutes of it — otherwise the note is left empty,
  because naming the wrong forecourt is worse than naming none. Range-extender cars only.
  _(beta discussion [#14](https://github.com/ProtossBlaster/MateBetaTesterOnly/discussions/14).)_

## [3.4.6] — 2026-08-01

### Fixed
- **A 72-second power-off is a power-off.** **@michapr** switched his car off between two drives and
  Mate still reported them as sharing one power-on session — *"the car was never switched off"* —
  and asked him to merge two trips that belong apart. Mate had seen the switch-off and thrown it
  away: a `ready=0` gap shorter than 90 seconds was treated as a signal blip.

  That 90 came from *"signal blips seen in the log"* and had never been measured. It has been now,
  across eight diagnostic bundles from three owners: of **123 distinct power-offs in three weeks,
  only three fall under 90 seconds** — 38.8 s, 72.0 s and 89.8 s — and the 72 s one is his real
  switch-off. The blips the threshold was defending against amount to that single 38.8 s event. It
  is now **60 seconds**, which still absorbs that one and stops swallowing the rest.

  The same constant was quietly doing a second, unrelated job: the slack that lets a session still
  be matched to the trip it belongs to, sized on the ~1 minute the gear-P trip-end lags behind
  ready-off. Lowering both together would have left that slack exactly equal to the lag it exists to
  cover, so the two questions now have two numbers. _(beta [#19](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/19).)_

## [3.4.5] — 2026-08-01

### Added
- **A stretch where the signal was lost now draws as a dashed bridge, not as a road you drove.**
  Every map — the global Map, the report's month map, a trip's own map — joined the recorded GPS
  fixes with a plain straight line, including across a real dropout: a tunnel, a dead zone, a cloud
  hiccup. That line cuts through buildings and fields, and nothing distinguished *"the car really
  drove this straight"* from *"we lost it here"*.

  A jump much larger than **that trip's own** typical sampling interval (its median × 3, never under
  a minute) is now its own two-point run, drawn thin, dashed and magenta, while every normal stretch
  keeps the solid line it always had. The threshold being relative is the point: it follows whatever
  polling cadence you have configured instead of assuming one. It is the same honesty the trip's
  SoC/speed chart already applied to a hole in its data by leaving it blank rather than joining
  across it. Verified against a real 322-trip history, where it found **nine such gaps across four
  trips** and put the dashes exactly where the car had gone quiet. _(#209, by **@hubcasale**.)_
- **"Trips shown" on the Map.** A long history leaves the map a solid mass of overlapping lines, so
  the legend row gains a box capping it to the N most recently driven trips — and because the point
  budget is then spent on fewer trips, each one that is drawn hugs the real road more closely.
  **0 = every trip**, which is the behaviour you have today, so nothing changes until you set it.
  _(#209.)_

### Fixed
- **A test that was named after a rule it never reached.** The one-minute floor under the gap
  threshold is what stops an ordinary drive that misses a few polls from sprouting dashed bridges —
  and removing it left the entire suite green, so nothing was holding it in place. The test named
  for it exercised the multiplier instead, as its own description conceded. It now has a case that
  fails when the floor goes, and the neighbouring one is named after what it really checks.

## [3.4.4] — 2026-08-01

### Fixed
- **A charge the car drove away from is recorded again.** A charge session was closed in exactly one
  place — when the car went from *charging* to *parked*. **@mikeeeeekoo** charged overnight on a
  Tesla Wall Connector, the Leapmotor cloud then refused three logins in a row (*charging → offline*),
  and an hour later the car turned up already on the road (*offline → driving*). Neither step matched
  that one condition, so the session was never closed: his log has `Charge #3 started` and no
  `Charge #3 ended` anywhere after it. An open charge is in no calendar and in no AC count, which is
  exactly what he reported — a full 12 → 100 % charge that Mate appeared not to have seen. Nothing
  was lost; the row was there all along, waiting for an end.

  The guard was asymmetric: plugging in **does** close a trip in progress ("plug inserted while
  driving"), but setting off did not close a charge in progress. It does now.

  **Which reading it closes on matters more than it sounds.** Not the one where the car is driving:
  his charge really ended at 100 %, and by the time Mate saw the car again it read 98.1 % — ten
  kilometres of road, not two points that never went in. The end is taken from the last reading made
  **while charging**, dated by the **car's own clock** rather than by when we happened to poll it:
  while the car is out of touch the cloud keeps re-serving the last frame it holds, and that frame is
  the last real measurement. On his night that is the difference between *06:10, 100 %* and *09:36,
  98.1 %*. One exception, and it is a measurement rather than a guess: a car whose odometer has not
  moved cannot have spent anything, so if it reappears **higher** than that last reading it kept
  charging while we were blind, and the fresh reading wins.

  The same rule now applies to a session found open at startup (crash recovery): it used to close on
  the last position of *any* kind since the charge began, which for him would have been a whole
  morning of driving — 12.8 → 92.3 %, ending at half past noon. So a charge already stuck open in
  your database gets closed correctly by this update rather than badly. _(#208.)_

### Changed
- **The Features list on the README was rebuilt.** Thirty essay-length bullets became a grouped list
  of one-line entries, and the two languages are now **verified to match, item by item**: they had
  silently drifted apart, and the Italian half was missing **V2L monitoring**, the **battery-health
  page** and **trip notes** entirely — three features an Italian reader could not learn Mate had.
  While checking each claim against the code, the polling interval turned out to be documented wrong
  in **both** languages: it reads *"configurable 10–30 s"*, and it is really **10 s to 10 minutes**
  while parked and **10–60 s** while driving.
- **MateDesktop is now mentioned.** It has been public since 26 July and appeared in no file of this
  project: it joins the README as **Option C** (in both languages) and the Docker Hub page, with the
  note that Windows ships inside a `.zip`. The Docker Hub page also claimed four interface languages;
  there are **seven**.

## [3.4.3] — 2026-08-01

### Changed
- **The BetaTester banner told testers not to report anything, and one of them almost didn't.**
  Every page of the beta build carried a single instruction — *"do NOT open issues about
  inconsistent data"* — written to hold back a flood of *"my costs look wrong"* on a REEV, which is
  expected while REEV behaviour isn't integrated. It held back more than that. A tester found a
  real defect — **one refuel filed three times** — and wrote it as an afterthought at the bottom of
  a report about something else, because, in his words, *"it says all over the place not to report data
  inconsistencies"*.

  The banner now draws the line where it belongs. A figure that merely looks off still needs no
  issue; anything that looks **broken** — an event counted twice, a duplicate row, a value that
  cannot be true — is worth reporting. The same split replaces the old sentence on the one-time
  consent screen. All seven languages. The beta repository's issue form, its acknowledgement
  checkbox, both READMEs, the add-on documentation, the consent notice and the add-on store
  description carried the same discouragement in seven more places and now say the same thing.
  _(beta [#17](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/17), @pdifeo.)_

## [3.4.2] — 2026-07-31

### Fixed
- **The shared-session message now says WHICH trips, instead of "the adjacent one".** When Mate
  can't separate a trip's official figure from its neighbours it asks you to merge them — and named
  the neighbour as *"the adjacent one"*, which says neither *previous* nor *next* nor *how many*.
  The reporter had to come back and ask which trip was meant; his was the previous one. The message
  now lists the other trips by their start time, so a session holding three of them no longer
  describes itself in the singular either. All seven languages. _(beta [#19](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/19), @michapr.)_

## [3.4.1] — 2026-07-31

### Fixed
- **Merging two trips could throw away the petrol one of them burned.** On a range-extender, a
  merged trip took its fuel readings from the **parent row alone** — and the parent is the *earlier*
  of the two. Merge a short electric hop with the long generator-on drive that followed it and the
  group inherited the hop's untouched tank: the litres vanished, the ⛽ marker went with them, and
  the trip's cost fell from **7.53 € to 0.50 €** because only the electricity was still being
  counted. Distance, duration, SoC, regen and elevation had always spanned the whole group; fuel had
  simply never been added to that list, and it now spans it the same way — first segment's reading
  at the start, last segment's at the end, skipping segments that carry none.

  Nothing was lost from the database: the segments kept their own readings throughout, so every
  merged trip already in your history reports its fuel again with no re-recording, and unmerging was
  never necessary. The same figure feeds the trips list, so the ⛽ marker returns there too.
  _(beta [#20](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/20), reported by @michapr — who hit it by following Mate's own advice to merge.)_

## [3.4.0] — 2026-07-31

### Added
- **A trip that ends with more charge than it started now says so, instead of showing nothing.** On a
  range-extender the generator can put back more than the motor took out; the energy tile on such a
  trip was simply empty. It was not a hidden value — the number is computed when the trip closes and
  then discarded, because a consumption per hundred kilometres means nothing while the pack is being
  refilled mid-drive, and that rule is right. So the tile now carries the **battery's net change**
  with its sign: `−2.9 kWh` where the pack ended fuller. Derived from the trip's own SoC pair, so
  every trip already recorded has it, with no migration and nothing to re-record.

  It is deliberately **not** the same figure as the consumption beside it: that one is the gross
  energy that left the pack, which stays positive even on a trip you finished with more charge. Both
  are true, they answer different questions, and putting a minus sign on the second would have been
  the wrong number under the right label. Where the pack drained normally, the net stays out — the
  consumption figure already says it, and one trip should not print two energy numbers.
  _(beta [#11](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/11), reported by @michapr and @gm27271.)_

### Changed
- **"From the car's own gauges" now reads "Measured by the car".** The old wording made the figure
  sound like a second opinion when it is the energy you actually have to put back in. All seven
  languages. _(beta [#11](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/11), @michapr.)_

## [3.3.1] — 2026-07-31

### Fixed
- **A message about your car said something Mate cannot know.** When the official per-trip figure
  can't be separated from the neighbouring trip, Mate offered to merge them and explained why with
  *"the car was never switched off between this trip and the adjacent one"* — a statement about the
  vehicle, presented as fact. A range-extender owner showed it was false for him: his raw signals
  have the car going off at 07:55:21 and back on at 07:56:33, a real 72-second switch-off between
  two drives. Mate saw it and discarded it, because it ignores power-off dips shorter than 90
  seconds — the signal does blip, and a blip would otherwise split one drive in two. His was the
  only one of 28 power-offs in three weeks to fall below that line; the next shortest was 120
  seconds. The threshold stays: measured against a dense signal log, lowering it to 45 seconds
  rewrites the session of 175 trips out of 300, and two attempts to tell a blip from a brief
  switch-off structurally didn't separate them. So the wording changed instead, to what Mate can
  actually back — that it reads the two trips as one power-on session and the official figure
  covers both. Same advice, no claim about your car. All seven languages.
- **A known power state is no longer carried forward for ever.** Reconstructing that session means
  reusing the last reported power state across polls that didn't carry one, which is right for a
  missed reading and wrong for hours of silence: on a car that reports the signal rarely, one
  reading could keep meaning "still on" straight across a genuine power-off. It now expires after
  fifteen minutes, or three polls if you have widened the parked interval, and expires into
  "unknown" rather than "off" — a value nobody read is not evidence the car was switched off.
  No effect where the signal is dense: on a full-electric log carrying it in 89.8% of rows, all 60
  most recent trips reconstruct identically.

## [3.3.0] — 2026-07-31

### Changed
- **Merging two trips now happens inside the day, under the date.** The 🔗 button moved off the top
  of the page and sits beside the day's heading, appearing only once a day is open: press it and the
  calendar stays where it is while that day's list narrows to just the pairs you could join, gap
  slider and all. Before, it replaced the whole calendar with every joinable pair in your history —
  and a trip row prints a clock, not a date, so twenty-two pairs arrived as bare times with nothing
  saying which day any of them was. Two of them began at 17:52 and 17:53, weeks apart, four rows
  from each other. The day's heading already says the date; the pairs now sit under it.
  _(#204, reported by @riri19.)_

### Added
- **A trip you found by searching now says which day it was.** A result stands on its own — there is
  no calendar heading above it — and it showed only `17:52 → 18:15`, so the one thing you were
  missing was the one thing you had to go back to the calendar for. Charges learned this in v2.16.0;
  Trips never did. The date appears on search results only: in the day drawer it would repeat the
  heading directly above it, once per trip.

## [3.2.1] — 2026-07-31

### Added
- **Setup now asks for your time zone, pre-filled with the one it detected.** It is the only
  setting whose default can be silently wrong, and the wizard never asked: Mate simply used
  whatever clock it was running on. On Home Assistant that is your own zone and this is one more
  field to skip past; on a bare Docker container it is **UTC**, and now it says so — in the open,
  before there is any data to anchor wrongly. The full zone list, in all seven languages.

### Fixed
- **"Automatic" was never wrong so much as unrecorded, and that is what made it dangerous.** Times
  you type are anchored to the zone in force at that moment; on Automatic the setting stayed empty,
  so a charge you entered or imported was pinned to a clock nobody had named. The repair that puts
  such rows back **refuses to run without a chosen zone** — correctly, it will not bake in a guess —
  so those installs could never be corrected either. If the container ran on neither UTC nor your
  real zone, the offset stayed in for good.

  On update, Automatic is turned into the zone it was already resolving to, and written down.
  **Nothing moves and no time on your screen changes** — it is the same zone, now named. Two things
  follow: the old hand-entered rows can finally be repaired, and the setting stops silently
  following the container, so changing Home Assistant's zone no longer re-interprets what you typed.
  Pick a different one whenever you like; that has always worked and still does.

  Found while closing **@ghuaywen-ai**'s #181, whose 150 imported charges went in seven hours late
  and took three releases to put right. This is the fourth, and the one that stops it happening to
  the next person.

## [3.2.0] — 2026-07-31

> ⚠️ **If you have a wallbox meter configured, the cost of your past trips goes up on update —
> by around 8%.** Nothing was lost and nothing is being recalculated wrongly: money you had already
> spent was simply landing on no trip at all. The first entry below explains exactly which money.
> No setting changes meaning, no data is migrated, and nothing else in the app moves.

### Changed
- **A trip is now costed on the energy that reached the battery, not on the energy the meter
  billed.** These are not the same number. Charge at home and the wallbox counts what left the
  wall; the car's on-board charger turns 8-15% of it into heat, and only the rest arrives in the
  pack. Mate was dividing what you paid by the meter's figure, then applying that rate to energy
  taken *out of the battery* — so the kilowatt-hours lost in the charger, real money off your bill,
  were charged to no trip at all and the trips of a month never added up to the month's
  electricity. The divisor is now the energy that actually reached the pack.

  Measured on a real 302-trip history: **77,52 € becomes 83,90 €**, and 243 of those trips move.
  Only home charges **with a wallbox reading** are affected — every other charge already had one
  figure and not two, because without a meter there is nothing to disagree with.

  Trip costs have never been stored: they are worked out from the charge history each time a page
  is drawn, which is what lets a price corrected months later fix every trip after it. That same
  property is why this correction reaches the whole history at once rather than only new charges.

  **What is shown on the Charges page does not change.** The kWh on a charge card, the period
  totals and the €/kWh there still come from the meter, because there the question is what you paid
  at the wall — a different question with a different right answer.

### Added
- **Range-extender: the electricity you bought is now spent, and can run out.** A REEV's battery
  takes energy from two places but money from one. The socket adds kilowatt-hours *and* euros; the
  generator adds kilowatt-hours only — those were already paid for in litres, on the very trip that
  burned them. Mate used to price the pack at a rate that charges raised and nothing ever consumed,
  so an owner who charges once a month paid grid rate for a month of petrol-made kilowatt-hours.

  Now what you bought is a stock that depletes: trips draw on it in order, and once it is gone the
  electric line of a trip is **0,00 €** — not a fading remainder, zero, because there is nothing
  left that you paid for. Charge 28 kWh and drive 10 a day and the electricity is spent on day
  three, exactly as it is in the car. A trip shows how much of what left the pack had been bought
  and how much came from the generator. From the discussion opened by **@michapr**, with
  **@gm27271**.

- **The tank on the Overview now shows litres, not only a percentage.** Range-extender build only.
  The figure comes from the car's own litre counter, which makes it the better of the two: the
  percentage is a float gauge, and converting it would need the tank capacity — something Mate
  assumes per model rather than measures. Mate has been reading that counter since v2.14.1 and no
  page had ever printed it. Asked for by **@rop12770** (#202).
- **A range-extender trip shows what it cost.** The cost tile keyed off the electric figure alone,
  so a trip run on a tank of petrol showed a dash while the fuel € sat in the block below it. It
  now shows electricity plus fuel, with both parts under it.

### Fixed
- **Mate was telling range-extender owners something untrue about their own consumption.** The note
  under the dual-energy block said the electric figure came from the car's metered `getEC` rather
  than the battery percentage, *because a running generator recharges the pack mid-drive*. Measured
  against two cars' data, `getEC` is roughly what **left the battery** — what the generator sends
  straight to the wheels never passes through the pack and is invisible to it. One week of driving
  reads 645 km against 20 kWh, which no car can do. The note now says what the number is, in all
  seven languages. It remains the right figure for the **cost** — what came out of the pack has to
  be paid for — and the wrong one for the **consumption**.
- **Maintenance item names were being cut off on a phone.** Each name was held to a single line and
  truncated with an ellipsis — fine on a desktop, where they all fit, and unusable on a phone: after
  the icon and the two buttons a name has roughly 165px, and 19 of the 20 Dutch names are wider than
  that. *Verlichting, claxon, ruitenwissers — controleren* needs 351px, more than double the room it
  had, so what you actually read was *Verlichting, claxon…*. Names now wrap and the card grows to
  fit. Reported by **@adoewa** (#201).
- **The three counters at the top of Maintenance had no room for their labels.** They sit in a fixed
  three-across row, so on a phone each tile is about 100px wide and the label inside is a single
  uppercase word that cannot wrap: Dutch *BINNENKORT* measured 83px and touched both edges, which
  looks like bad centring rather than a tight fit. It was centred — it simply had nowhere to go.
  Below 480px the letter-spacing goes and the type drops a notch, which is enough for every
  language. Italian *IN SCADENZA* needed 87px and did not fit at all, so this was ours too and
  nobody had said so.
- **Dutch: *recuperatie* is now *regeneratie***, in all five places it appears. Corrected by
  **@adoewa**, who translated the language in the first place and then went back over it.

## [3.1.0] — 2026-07-30

### Added
- **The price behind a trip's cost is now on screen.** A trip is costed at the average price of the
  energy in the battery when it started — every priced charge counted in proportion to the charge
  it added, so a tank filled half at home and half at a fast charger sits somewhere between the two.
  Mate has computed that rate since per-trip costs shipped and simply never printed it, which left
  the € with no way to check it short of redoing the arithmetic. It now appears under the cost on a
  trip, and under the battery on the Overview, where it is the rate your **next** trip will be
  costed at. Hover either for what moves it: charging does, driving never does. Asked for by
  **@riri19** (#200), whose description of the mechanism was exactly right.

### Fixed
- **The unit on *Best efficiency* was as large and as green as the number.** The `eff` filter hands
  back value and unit as one string, so `18.5 kWh/100km` went into the tile as a single lump and the
  unit inherited the number's size and colour — while *Avg consumption*, sitting right beside it,
  already set its unit small and grey. Both now match. Spotted by **@adoewa** (#199).
- **Two figures on a trip no longer sit at different heights.** Those cells are 130px wide, so
  whether a label wraps depends on the language: *Energia consumata* takes two lines where *Consumo
  medio* takes one, and the value below simply followed, leaving one number 18px lower than the one
  next to it while the rows above and below lined up. Every label in that grid now reserves the same
  two lines. The consumption unit is a step smaller there too — at the shared size `16.5 kWh/100km`
  did not fit the column and the unit dropped onto its own line, the same orphaned look as above.

## [3.0.0] — 2026-07-30

> Nothing in this release breaks anything: no setting changes meaning, no data is migrated, and an
> existing install updates in place as always.

### Added
- **Dutch — Mate now speaks seven languages.** The whole application: every page, the setup wizard,
  the login screen and the maintenance schedule. It is not a partial translation to be finished
  later — all 1117 strings are in, and a test now refuses any language that falls short of the
  others. Choosing it: **Settings ▸ Language & Currency**, or the flag on the first setup screen,
  which also picks Dutch by itself when the browser asks for it. Asked for by **@adoewa** (#187).
  A handful of labels are shorter than the literal translation on purpose — *Km-stand*, *Stand*,
  *Airco-doel* — because Dutch compounds are long and those three sit in fixed-width tiles where
  the full word was cut off. Every page was checked at desktop and phone width before release.

### Fixed
- **The Maintenance page was in English for Polish users, and always had been.** That page keeps
  its own dictionary inside `maintenance.py`, separate from the locale files, and Polish was never
  added to it — the community translation (**@irek**, PR #90) covered the locale files, which is
  where anyone would look. Nothing ever failed: a missing key falls back to English, silently, and
  no test reached that file. All 62 service items, categories and phrases are now in Polish, and
  the page title matches the sidebar the way it does in every other language.
- **The trip count in Statistics read "trips" in every language.** The year and month rows had the
  word hardcoded in the template, so Italian showed *58 trips* and German *58 trips*. Both rows now
  use the same singular/plural handling as the Trips calendar — *58 viaggi*, *58 Fahrten*,
  *58 ritten*, and *1 viaggio* when there is one.

## [2.19.3] — 2026-07-30

### Fixed
- **A charge with no position can be given a station name again.** The ✏️ free-text name (v2.17.0) was added at the end of a block guarded by *the charge must have coordinates* — a condition that belongs to the 🔄 lookup beside it, which searches OSM/OCM **around a point** and genuinely cannot run without one. Typing a name never needed it. So the pencil was hidden from exactly the charges that had no other way to be labelled: the ones entered by hand or imported from CSV, which carry no position by construction, and the ones the car recorded with **no GPS fix**, stored as 0,0. The two conditions are now separate. HOME still shows neither — naming your own wallbox as a public station means nothing. Reported by **@adoewa** (#197), who found it the only way anybody could: with older charges he had typed in himself.

## [2.19.2] — 2026-07-30

### Fixed
- **One tank of fuel is one refuel again.** A float gauge does not jump to the final level — it climbs there in steps, and the car reports every one. Measured on **@pdifeo**'s C10 (beta [#17](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/17)): 70.2 → 78.0 → 87.0 → 98.1 → 100.0 % in twenty-eight seconds. Mate read those pairwise and filed each rise as its own refuel, so one fill-up showed up as **three**. It now follows the fill while the level keeps climbing and records it once, absorbing the small final step as well — that step is under the detection floor, and dropping it was costing nine tenths of a litre off every full tank. His fill reads 33.390 → 47.500 L, one row. Tuning the floor could never have fixed this: raise it and you still get three, lower it and you get four. **This reached the beta testers running a range-extender** — the Refuels page needs both a REEV and the BetaTester build. _(Corrected 2026-08-01: this entry first claimed the page ships publicly and that every REEV owner was affected. It does not and they were not — the gate has been in place since 2026-07-13. The defect and the fix are unchanged; only their reach was overstated.)_ As a bonus his tank reads 47.500 L at 100 %, confirming the C10 capacity correction from v2.14.1 on a second car.

### Changed
- The comment describing when a charge session closes said it "only CLOSES when the cable is pulled". Measured on a real car over one night: when a load-balancing wallbox stops the current, the car reports the cable **gone** — so the session does close, and a single plug-in was recorded as six charges. Behaviour is unchanged; the comment now states what the data says, so the next reader does not inherit a false premise.

## [2.19.1] — 2026-07-30

### Added
- **Home Assistant can now see how old the car's data really is.** Two new MQTT sensors: **Data Timestamp**, the clock the *car* put on the frame it last sent, and **Data Age**, the seconds since. The existing *Last Seen* is when **Mate** last wrote a row — it stays a few seconds old for as long as the cloud keeps answering, even when what the cloud is answering with is half an hour stale. That gap is the whole point: it is the difference between a car that is genuinely parked and a car that stopped reporting while it was moving. Both go out **without** the driving-or-charging condition the Overview applies, because an automation should apply its own. Empty rather than 1970 on a car that doesn't report its clock. Asked for by **@riri19** (#178).

### Fixed
- **The kilometres driven before the cloud caught up are no longer lost.** When a car sets off somewhere without coverage, the cloud keeps re-serving the last frame it holds — gear P, speed 0 — so Mate stayed parked through the opening kilometres and then opened the trip with the odometer read *after* them. Those kilometres, and the energy that moved them, were dropped: the odometer-jump reconstruction couldn't catch them either, because it hands over to the live trip in the same poll. The trip is now anchored to the last reading taken before it, so the distance and the consumption are right. Its start time and start point are deliberately left alone — when the car set off is unknown, and a frozen frame's GPS is routinely 0,0. Reported by **@riri19** (#130, #129). No effect at all where the cloud link is healthy: measured against 303 real trips, none was touched.

## [2.19.0] — 2026-07-30

### Added
- **A trip's own map now marks where you charged at the end of it.** Same amber marker as the Map's stations, with a popup carrying the kWh, the cost and a link straight to the charge. What counts as "this trip's stop" is a time window — from this trip's end to the moment the car next moved — so no GPS guesswork is involved: the car cannot have charged anywhere else in between. Charges at your own wallbox are left out, or every drive home would earn a "charging stop" for parking in the driveway.
- **‹ › buttons on a trip, to step to the one before or after.** Chronological, through the same trips the Trips list shows: a merged trip has no page of its own, so the arrows skip past it to somewhere you can actually land. A trip with nothing on one side simply doesn't show that arrow.
- **The Map's station cap is adjustable from the map itself.** It has always drawn the 15 busiest spots; someone who charges in many one-off places could see an older single visit pushed out by newer ones. A small box on the legend row now sets it — 0 shows them all. Both features and the box come from **@hubcasale** (#195).

### Fixed
- A charge the car reported with **no GPS fix** is stored as latitude 0, longitude 0 — which is not the same as *missing*, and the new trip marker took it at face value: on the test data one landed **5 132 km** away, in the Gulf of Guinea, on a trip that never left Milan. It now uses the same guard the Map's station cluster has always used, where 0 counts as absent.
- The station-count box saves through a POST rather than a URL parameter. A page that writes a stored preference just by being loaded gets re-triggered by every bookmark, Back button and link prefetch that touches it — and on an install two people share, one person's link would change the other's map. The value is clamped too, like the marker-threshold setting next to it.

## [2.18.0] — 2026-07-30

### Added
- **The Charges page now tells you what a kWh actually costs you.** A sixth card next to the totals: your spend divided by the energy you paid for, quoted to three decimals — the one number that turns "I spent 101 €" into something you can compare against a tariff, a public charger or a litre of petrol. Asked for by **@adoewa** in #187, and it turned out he was asking for something Mate could already work out but only showed one month at a time, on another page.
- The card says what it covers. A charge with no cost yet — one you haven't tagged, or of a type you haven't priced — still has kWh, so leaving it in the division would report a price lower than the one you pay. It is left out, and a line under the number says so: *"25 of 26 sessions"*. Without that line the page would show a total energy and a total spend that divide into a **different** number from the one printed beside them, and nothing to explain the gap.

### Fixed
- **The monthly Report's "Avg price" was under-reporting, sometimes badly.** It divided the month's spend by the month's *total* energy, unpriced charges included. On the test data a single untagged charge out of ten was enough to show **0.199 €/kWh** for a month whose real price was **0.250** — a fifth off, and plausible enough that nobody would question it. Both places now compute the average the same way, from one function, so they cannot drift apart.
- The Report also quoted that price with two decimals, which flattens 0.250 and 0.199 onto 0.25 and 0.20 — hiding both the error and the correction. Prices per kWh now keep three decimals, like the per-litre prices already did.

## [2.17.0] — 2026-07-29

### Added
- **You can now type a charging station's name yourself.** Every station name in Mate comes from OpenStreetMap and Open Charge Map, and some real stations are in neither: a company car park or any charger behind a barrier is invisible to both **by design**, so the 🔄 lookup could only ever come back empty on them — exactly **@adoewa**'s case in #193. A **✏️** button next to the lookup opens a small field: type the name, press ✓, done. It opens pre-filled with the current name, so it doubles as a quick correction; an empty submission changes nothing; and a typed name takes the charge out of the background lookup queue exactly like a found one would. One thing worth knowing: 🔄 still re-runs the database lookup and replaces the name when it finds exactly one match — press it only when a re-lookup is what you want. Written overnight by **@hubcasale** (#194), on top of yesterday evening's release, with all six languages covered.

## [2.16.0] — 2026-07-29

### Added
- **The Scheduling page now says what your car is actually doing, before the form that changes it.** Both cards open with a line in plain words — *"Charges every day from 00:50 to 12:00, up to 100%"*, *"Quick cool once only at 07:00, 18°C"* — built from the car's own answer, with no extra call. **@riri19** selected all seven days of his charge schedule, saw the chips empty again after a reload and reported the schedule as lost (#190). Nothing was lost: on that card **no day selected means every day**, which is also the state every car leaves the factory in, so the row is empty for almost everyone. The chips still work the way they did — you pick the days you want rather than deselecting six of seven — but now the card says what the empty row means.
- The same line on the **climate** card, where it was needed more: an empty day row there means the **opposite** — one time only, not every day. Two cards, one under the other, identical rows of chips, opposite meanings, and nothing on screen to tell them apart. It also spells out the mode ("Quick cool" rather than a payload), the time and the temperature, and says plainly when nothing is scheduled at all.
- **A charge found by search now shows its date.** In the calendar the day is the heading above the cards; a search result stands alone, and it showed only "16:38 → 16:42". **@riri19** looked up a station by name and had to go back to the calendar to find out which day it was (#191). Same date format the history uses. Unchanged in the calendar, which already says it once.

### Changed
- The litres on a detected refuel now say they are yours to overwrite: *"gauge estimate — replace it with the litres on your receipt"*. The box was always editable, and the amber "≈ 35.15 L" above it reads like a figure the car produced, so people leave it. **@gm27271** worked out the consequence before hitting it: confirm 9 litres against a 10-litre receipt and the price per litre comes out 20/9 rather than the 2.00 actually paid — and that price weights the blend behind every trip that burns from that tankful. Range-extender research builds only.

## [2.15.0] — 2026-07-29

### Added
- **A charge you typed in can now be corrected — all of it.** Until now, once a past charge was in, the only things you could still change were its note, its AC/DC tag and its cost; the times and the battery levels were fixed for good. **@adoewa** entered his entire charging history from a spreadsheet and then found no way to fix any of it (#188). Every charge you entered by hand — through the form or the CSV import — now carries an **✏️ Edit** button holding the start, an optional end, the energy, the cost, AC/DC and the two SoC readings, filled in with whatever is already there. Give it an end time and the session gets its duration back too.
- The button is on charges **you** typed and on no others. What the car measured stays as the car measured it: those numbers are readings, and a form over them would let one keystroke overwrite what actually happened. Mate now marks a charge as hand-entered when it is created, and the existing ones are recognised on update — the "Manual" tag on the badge could never do that job, because it also means *"I'll type the price myself"*, which people rightly use on real charges.

### Fixed
- **A charge with no battery reading no longer shows one.** A typed-in charge carries no SoC — the form has never asked for it — and the card was drawing that absence as a measured **0.0% → 0.0%**, with a yellow **+0.0%** beside it and an empty bar underneath. The figures were not wrong so much as invented, sitting next to real ones. Unknown now reads as a dash, and only a genuine zero still prints as 0.0%.
- **Changing your time zone can no longer move a charge the car recorded.** Since v2.12.1 Mate re-anchors hand-entered charges when you pick a different zone — correctly, because a time you typed is a time on your clock. It found them by their "Manual" tag, and that tag has a second meaning: it is also what you pick to type the price of a **real** charge Mate can't tariff by itself, which is a perfectly ordinary thing to do on a public charging session. Such a charge was therefore in scope, and while the first pass left it alone, a later change of zone shifted it by the whole offset — a session the car timed at 07:54 came back at 13:54 after a move between two continents. Found while building the edit form above, which needed to tell the two meanings apart anyway; the repair now asks the same, narrower question. Nothing you typed is affected, and no charge moves on update.

## [2.14.1] — 2026-07-28

### Fixed
- **Litres now come from the car instead of from an assumption — and the assumption was wrong on the C10.** Every figure Mate has ever shown in litres was the fuel gauge's percentage multiplied by a tank size taken from a spec sheet: 50 L, the same for every range-extender. **@gm27271** decoded the signal that carries the litres the car counts for itself, and it measures the tank as a side effect — **47.5 L on a C10, 50 L on a B10**. So a C10 owner has been reading litres **5 % too large** all along: the fuel burned on each trip, the L/100 km, the level in the tank, the weights behind the blended price per litre, and the litres of a refuel Mate spots by itself. From now on the car does the counting wherever it reports it, and the per-model size is only the fallback for trips recorded before this update. He also showed why it is worth having: his own fill measured **34.416 L** where the pump ticket said 33.84.
- **Exporting your trips to CSV works again if you have ever merged two of them.** The button returned a page whose whole content was the words *Internal Server Error* instead of a file — not now and then, but on every attempt, for anyone with a single merged trip anywhere in their history. A merged trip carries one extra internal field that an ordinary trip doesn't, and the exporter took its column list from the first trip alone, so it fell over the moment it reached a merged one further down. Found and fixed by **@hubcasale** (#184), who traced it from the downloaded file's own contents; the columns are now the union of every row's, and internal bookkeeping fields are left out rather than printed as raw Python. Neither CSV export had a single test before this — they have six now.
- **A car model Mate hasn't met before can still read its own data.** Of every call Mate makes, exactly one carries the model in its address — the one that reads the car's live status — and the list of models that need a different address than their own name has two entries in it. Anything else asks for an address the Leapmotor servers don't answer, and the result is the most confusing failure there is: the login works, the car is there with the right VIN, the right model and the whole list of things it can do, the official app on the same account shows live data — and Mate shows nothing, from the first minute, for ever. **@arnolds77** spent two days on it with a B05, changing accounts and permissions that were never the problem. Mate now tries the family address once when a model's own one is refused, and remembers the answer — one extra call, once, on an install that was getting nothing anyway. Cars that already work are untouched and never make the extra call.
- **Restarting Mate while your car is stuck no longer records one frozen reading as if it were live.** When a car's telematics goes quiet the cloud does not say so — it hands back the last frame it received, over and over — and Mate has recognised that since v2.5.16 and refuses to store the repeats. But the marker it compares against lives in memory, so the first reading after any restart could never be seen as a repeat. **@riri19**'s B10 had been parked for two hours with its telematics silent, the cloud replaying a frame from 17:16; his Mate restarted at 18:51:03, and 18:51:05 went into his database as a fresh reading at 116 km/h. Mate now reads that marker back from disk on startup, the same way it already did for the battery level and the odometer.
- **Choosing ECO or Custom as your default drive mode now actually tags your trips.** Both were added to the list two releases ago, but only to the half of Mate that draws the Settings page — the half that stamps the tag on a new trip kept its own older copy of the list and threw anything outside it away. So the setting saved, came back selected, and quietly did nothing: every screen agreed with you and the trips came out untagged. Reported by **@adoewa**, who picked ECO the day it appeared. The three modes that already worked are unaffected, and trips you have tagged by hand were never involved.
- The two lists are now checked against each other, because that is the fault here: not a wrong value, a second copy nobody updated. A test that had used "eco" as its example of *an invalid mode* was still passing — it was asserting the bug.

### Changed
- Beta bundles now carry the full reply from the cloud's mileage/energy endpoint, not the two figures Mate reads from it. On a range-extender the lifetime average is electricity divided by a distance partly driven on petrol — **@michapr**'s car reports 12.6 kWh/100 km over its electric kilometres where Mate reports 8.9 over all of them, and **@gm27271** described the same thing from the other side. The split that would settle it is in none of the endpoints we had ever captured; if it exists it is in a field that call currently discards. Range-extender research builds only.

## [2.14.0] — 2026-07-28

### Added
- **Mate now spots your refuels by itself.** A tank can only rise one way — nothing recuperates into it, nothing refills it while you drive — so when the car's own gauge goes up and stays up, somebody put fuel in. Each rise turns into a card on the Rifornimenti page carrying **when** and **how much**, and leaves you the one thing the cloud will never know: what you paid. Confirm it and it becomes an ordinary refuel; say it wasn't one and it never comes back. Asked for by **@gm27271**, in almost exactly this shape — *"there will be no data entered, this can be added later by the user"*. Range-extender cars only.
- It reads the history rather than watching from now on, so **the refuels from before this update are already there** the first time you open the page. What it claims is bounded by what a fuel gauge can tell you, and the card says so rather than pretending: the instant is an **interval** — after the last reading at the old level, by the first at the new one, which on a car that fell asleep at the pump can be the whole night — and the litres are an **estimate** off a float sensor, ~1 L being the smallest rise it will act on. Both are yours to correct before confirming.
- Confirming beats retyping for a reason beyond convenience: the refuel is filed **at the moment it happened**, with the exact tank level measured just before it. Typed by hand hours later, that residual is whatever the tank held when you got round to it — and it is what weights the blended price per litre.

## [2.13.3] — 2026-07-28

### Added
- **The Overview now tells you when what you're looking at isn't current.** "Last seen" is the age of Mate's last poll, and the cloud always answers — so when your car drops out of coverage it keeps handing back the last frame it received, and the screen says "13s ago" over a position and a battery reading from half an hour before. **@riri19** described that trap precisely. Mate has always known the car's own timestamp on each frame; it just never stored it. It does now, and when the data has fallen behind, the line grows a tail: *Last seen 13s ago · data 33m old*. It is deliberately quiet the rest of the time — it appears only when the car was **driving or charging** (a car asleep in a garage legitimately has hours-old data, and saying so every morning would be noise), and only when the data is genuinely behind Mate's own polling rather than both being old for the same reason.

### Fixed
- **"Last seen" now speaks your language.** It was English on every install — *13s ago* under an Italian, German, French, Polish or Portuguese label — on the Overview card and in the map popup. It had been standing alone long enough that nobody noticed; putting a translated phrase beside it made it obvious.

## [2.13.2] — 2026-07-28

### Fixed
- **The car's picture no longer hammers your Leapmotor account when it can't be downloaded.** Mate keeps the image package on disk and serves it from there — but that short-circuit can only save a request once the download has succeeded at least once. On an install where it never had, every single refresh of the Overview went back to the cloud: two attempts, each resetting the session, every 30 seconds, for as long as the page stayed open. From the cloud's side that is a login storm, and it answers a login storm by refusing — **@arnolds77**'s log carried 42 session resets and 35 refusals in 45 minutes while he was trying to work out why his car had no data. Mate now tries once and waits before trying again; pressing refresh on the image still goes straight to the cloud, because that's you asking. The once-per-restart re-download that picks up a repainted car is unchanged. The picture is decoration — it had no business spending the session everything else depends on.

## [2.13.1] — 2026-07-28

### Fixed
- **The correction v2.12.1 made to hand-entered charges now waits until you've chosen your time zone — and follows you if you change it.** Yesterday's fix converted those charges using whichever zone was configured the first time Mate started after the update. Installing first and picking your zone afterwards is the ordinary order of events, so an install could be converted as though it were on UTC and then consider the matter closed: **@ghuaywen-ai**'s charges were still eight hours out while Mate believed it had corrected them. Nothing is converted now until a zone has actually been chosen, and the zone used is remembered — set or change it later and the charges that pass converted are re-anchored to it, giving back the times you originally typed. Charges you entered *after* the conversion are deliberately left alone: moving to another country doesn't change when you plugged in.
- If you updated to v2.12.1 without ever choosing a time zone, that conversion can't be undone automatically — v2.12.1 didn't record what it assumed. Choose your zone and any charge still shifted can be corrected by deleting it and importing your original file again, which now anchors correctly.

### Added
- **A month calendar for refuels, the same one the Charges page has.** Days you filled up carry their litres and how many stops, the month line totals litres and cost, and opening a day lists each refuel with its time, price per litre and the tank level before it. Asked for by **@gm27271** and seconded by **@michapr**; range-extender cars only, like the rest of the fuel section.

## [2.13.0] — 2026-07-28

### Added
- **Your car's charge window, on the Overview.** Mate has known it for a long time, but only inside the Scheduling page — so at the one moment it answers a question, it wasn't there: cable plugged in, nothing happening, and no hint that charging simply starts at 22:05. It now shows as a chip under the car whenever the cable is in and nothing is flowing, which is exactly when you'd wonder. It never appears while the car is actually charging. From **@rop12770**, who sent the official app's own banner. The window is read from the car every half hour and cached, so the page that redraws itself every 30 seconds never costs you a request; changing it from Mate — the Scheduling page or a Home Assistant automation — updates the chip immediately.
- **Two more drive modes to tag a trip with: ECO and Custom.** The Leapmotor cloud never reports which mode you drove in, so this is a label you attach by hand — and the three Mate offered didn't match any real car. **@adoewa** photographed his C10's own screen: ECO · Comfort · Sport · Custom, with no "normal" at all, while **@gm27271**'s range-extender shows Sport · Normal · Individual. One list now covers both, and every trip you have already tagged keeps its tag.

### Fixed
- **The day you open in the Trips calendar shows its own totals again.** Distance, ♻️ regen, cost and a distance-weighted efficiency — the same four the old year/month/day list carried on every level, and which quietly went missing when the calendar replaced it. **@ghuaywen-ai** noticed the regen figure was gone; it had been, since v2.9.0. The cost now closes the line, lining up with the month total right above it. Regen stays hidden on a range-extender, where a battery being refilled mid-drive can't be told apart from braking.

## [2.12.1] — 2026-07-28

### Fixed
- **Charges you enter by hand are no longer pushed forward by your time zone.** A time you type is a time on *your* clock, but it was being stored with no zone attached — and everything Mate stores is UTC, so the page read it back as though you had typed a UTC time and added your offset on top. **@ghuaywen-ai** imported 150+ charges and found every one of them seven hours late. This was never only about the CSV: the *Add a charge* form on the Charges page did the same thing, quietly, for everyone outside UTC since the feature shipped. **Charges already in your database are corrected on the first start after updating** — each one with the offset that was in force on *its own* date, so a January charge and a July one are not moved by the same amount. If you had been compensating by hand, those entries will now show the time you actually meant, which may be an hour or two from where you left them.
- **The charges file Mate exports can now be imported back into Mate.** The export is a full dump of what the database holds, so its first column is an internal id — and the importer, which read columns by position, tried to read that word as a date and rejected the file on its first line, then on every row after it. **@adoewa** hit exactly that after exporting his charges to keep them safe across an update. The importer now reads the header and matches columns by **name**, so it accepts both the template we hand out and Mate's own export; columns it doesn't recognise are ignored. Nothing was taken out of the export — it still carries the location, power and duration for anyone who opens it in a spreadsheet.
- **A range-extender's cable code no longer looks like charging.** The cable reports a value while the car is *driving* that is not a connection at all. One of the two readers of that signal already knew to ignore it; the other didn't, and could open an empty charge session at the moment a stale speed reading lined up with it. Nothing was ever recorded — the empty session was discarded — but the two now agree. Found while going through **@ebagnoli**'s captures. Range-extender cars only.
- **And the same signal's "still connected" state now means the same thing on both sides of Mate.** The poller has counted it since v2.8.4, when reading it as *unplugged* was shredding slow AC charges into fragments; the web half never got that change.

## [2.12.0] — 2026-07-27

### Added
- **Range-extender cars: what the driving actually cost, on every trip.** Mate leaves the efficiency figure blank when the generator has been running — a battery that is being refilled underneath you stops measuring how efficiently it drove you — and that left the long trips, the expensive ones, with no number at all. Statistics now carries **From the car's own gauges**: what left the battery beside the litres burned, across every trip, generator ones included. Proposed by **@michapr**, who also ran into it from a different direction than **@gm27271** did in the same week. Range-extender cars only; nothing changes for anyone else.
- **And beside it, what you actually bought.** The first card works both sides out from percentages. The second one doesn't have to: it adds up the charges Mate recorded and the refuels you entered, reads the electricity **at the meter** rather than at the battery, and shows the money. The two are not the same question and are not meant to agree — one is what came out over those kilometres, the other is what you paid for in that period.

### Fixed
- **The two buttons over the idle-drain chart no longer use the same word for different things.** One pair chooses how the value is expressed — a rate per 24 hours, or the battery actually lost. The other chooses how the bars are grouped — one per park, or one per calendar date. While both said "day" it looked like the second pair changed the calculation. It doesn't, and the rate everyone expects is already on by default. **@riri19** asked twice in one week and was right twice; the second button now says *per date*, in every language.
- **Charge detection: the setting now says what it measures.** "Minimum charging current" reads like the socket's rating, when it means what the car is drawing at that moment — and a domestic 230 V socket only ever delivers around 3–4 A, so a threshold nudged above that quietly loses every slow charge. The default was already right at 2 A; only the sentence was misleading. Reported by **@michapr**.

## [2.11.1] — 2026-07-27

### Fixed
- **The calendar now shows which day you are looking at.** Picking a day filled the list below it, but the highlight stayed on today — so the page told you one thing and showed you another. The day you chose is now ringed, and today keeps its own amber number: when they are the same day you see both. Trips, Charges and Wallbox — all three calendars had it.
- **And it survives a refresh.** These pages reload themselves every half minute when left alone, and that reload used to take the day with it: ring gone, list gone, back to nothing selected. Your choice now comes back with the page, list included, the same way your place on the page already did. Following a link to one particular trip or charge also lands with its day ringed, which it did not before.

## [2.11.0] — 2026-07-27

### Added
- **Compare the same journey across different days.** A 🔎 button on any trip with a GPS track lists every *other* trip that took the **same road** — not merely one that started and ended nearby — so the commute you drive twice a day finally becomes one series you can read: efficiency against outside temperature, against traffic, against the time of year. Each row carries its own overlap figure, so you can see how confident the match is. From **@hubcasale** (#174), answering a request from **@riri19**.
- Matching happens in two stages, both pure local arithmetic on coordinates already stored — no network call, nothing sent anywhere. A ~150 m cell on the start and end narrows the field; then the actual recorded path of each candidate is resampled every 100 m and compared with the trip you are looking at, and only a ≥70 % overlap counts. That second stage is the one that matters: endpoints alone would happily match a day you took a completely different road. The return leg is deliberately a separate group, because uphill and downhill are not the same drive.

### Fixed
- **The comparator no longer holds up the rest of the interface while it runs.** Its cost grows with how long you have owned the car — it re-walks the GPS track of every candidate — and it ran on the thread that serves every other page, so on a long history the whole app would sit still for a second or more. It now runs off to one side.
- **A trip whose first GPS fix never arrived is no longer filed under the wrong place on Earth.** When the car reports no usable position, Mate stores a zero rather than nothing, and the new route index treated that as a real location off the coast of Africa — which also meant such a trip could never match its own siblings on the commute it actually belonged to. It is now left unindexed, exactly as a freshly recorded trip already was.

## [2.10.2] — 2026-07-26

### Added
- **Trips and charges can write their own note — address, time and temperature.** A 🧭 button on any trip or charge builds a one-line summary and puts it straight into the note field: for a trip, the reverse-geocoded start and end address with the outside temperature already collected for it; for a charge, the station's address (the same lookup the 📍 label runs, skipped for home charges) with the times and the car's own outside and battery temperatures at each end. New trips and charges get it **by themselves** the moment they close — but only into an empty note, so anything you have typed is never touched. The button always overwrites, and asks first when there is something to lose. From **@hubcasale** (#171).
- **A switch to stop that happening by itself** — Settings ▸ Geocoder. Writing the note means looking an address up online, and a trip's two ends are, for most people, home and work; that should be a choice rather than a footnote. It ships **on**, and turning it off leaves the 🧭 button exactly where it is: the note stays one tap away, it just stops happening unasked.

### Fixed
- **The setup wizard showed the word "undefined" in French and German**, where the warning to use a Leapmotor account dedicated to Mate belongs — the single most important line of the whole setup, and the cause of the most common problem people report. The wizard keeps its own set of strings, separate from the rest of the app, and that one had never been translated; JavaScript renders a missing string as the literal text "undefined". Anyone setting Mate up in those two languages simply never saw the warning.
- **German and Polish were missing 80 and 91 strings**, so parts of the app appeared in English. All six languages now carry all 1027. Nothing was checking any of this, which is why both faults survived: **it is now checked** — every language must match English in both directions, every wizard language must match too, and the accepted-language list in the code must agree with the files on disk.
- **Range-extenders no longer announce "Fully charged" while the car is filling.** Signal 3736 was mapped as "charge completed"; nine complete charges from a B10 REEV show it turns **on** when a charge starts — cable in, current flowing, hours remaining — and off when it ends. On a REEV it means "a charge is running", so Mate now says "plugged in" rather than repeating a completion it cannot establish. What replaced it was a tolerance fitted to the first report, which hid the fault early in a charge and let it through near the end — where it was most likely to be seen. Fully-electric cars are untouched. Found from **@michapr**'s diagnostic bundles (beta [#12](https://github.com/ProtossBlaster/MateBetaTesterOnly/issues/12)).

## [2.10.1] — 2026-07-26

### Added
- **Removing Mate, from inside Mate — desktop app on macOS.** macOS has no uninstaller: dragging an app to the Bin takes the app and leaves everything it ever wrote behind, in a Library folder most people never open. Settings → App now has a button that does the whole thing — it stops Mate, deletes the database, the settings, the certificate and the caches, and puts the app itself in the Bin. It asks first, in plain words, and says that the data goes for good and to take a backup from Export / Backup if you want to keep it. It removes **only** Mate's own files, by exact path: the official Leapmotor app stores its data right next to Mate's, and nothing here goes looking for anything by name. The button appears only in the desktop app on macOS — Home Assistant and Docker have their own way of being removed, and on Windows the system's own uninstaller does this properly already.
- **Mate says when a newer version of the desktop app exists.** The app keeps Mate itself up to date on its own, but the shell around it — the Python runtime and the libraries — is released separately and changes rarely. When a newer one is published, an amber ↑ now appears beside the app version with a link to the download. It is a note, not an alarm: nothing is broken, and everything keeps working until you get round to it.

## [2.10.0] — 2026-07-26

### Added
- **Charges, Trips and Wallbox are now a calendar with a search bar**, instead of one long year/month/day accordion. Opening any of those pages used to render your entire history server-side, every time — slower the longer you have used Mate, and with no way to find anything except by scrolling. Now the page arrives as a month of day totals, and clicking a day loads that day. Charges and Trips also gain free-text search (station name or note; trip note) with filters: charge type, cost, kWh and date for charges; drive mode, distance, duration and date for trips. Distance is typed in your own units. From **@hubcasale** (#163).
- **Merging trips has its own view.** The old 🔗 mode revealed connectors inside the rendered tree, which no longer exists now that one day is in the page at a time. The button now opens a list of the pairs that can actually be joined across your whole history, with the same gap slider and the same preview before you commit. From **@hubcasale** (#163).
- **The Wallbox page no longer asks Home Assistant about every session up front.** It compared AC against DC for the whole history to show thirty rows; now it asks only for the day you open. From **@hubcasale** (#163).

### Fixed
- **A rest-drain estimate now says WHY it is uncertain.** A park of nearly two days, properly closed, was labelled *"uncertain estimate (short stop)"* — it was not short. Two independent things make an estimate untrustworthy: too little time to extrapolate a rate from, or too small a drop to tell from sensor rounding. The label only knew how to say the first, which sent you looking for a fault in the wrong place. Reported by **@riri19** (#160).

### Changed
- **Mate asks for confirmation in its own dialog.** Deleting a charge or unlocking the car used to raise the browser's own box, which announces the address Mate is served from — *"192.168.1.50 says"* on Home Assistant, *"127.0.0.1:4000 says"* in the desktop app. The question is now asked in a panel that looks like the rest of Mate. Escape and clicking away cancel; nothing is sent until you say so.

### For the desktop app
Only visible inside the standalone app — Home Assistant and Docker never see any of it.
- **A "Start at login" switch**, in Settings → App. Mate records only while it is open, and this is the part of that you can do something about. The switch stores nothing but a yes/no: everything that knows what a login item or a registry entry is lives in the app itself, so the same switch drives both macOS and Windows.

## [2.9.0] — 2026-07-25

### Added
- **Charging stations you've used now carry a link to their own listing page.** A 🔗 next to the station name on the Charges page opens it on OpenStreetMap or Open Charge Map, where you can see the sockets, the photos and whatever else the community has recorded about it — useful when you're deciding whether to go back. Charges labelled before this version have no link saved; **Settings → Charging stations → Recover missing links** fills them in without touching the names you already have. The same link, and the street address, now also appear on the results of **Find chargers** — including the TomTom ones, which have no page of their own and borrow one from whichever other source covers the same spot. From **@hubcasale** (#162).
- **A 📍 recalculate button on each charge**, for when the name Mate picked isn't the one you'd use. The automatic pass has to choose in silence and takes the nearest match; pressing the button runs the search again and, when the sources genuinely disagree — a service area is routinely listed under the network's name by one source and the site's name by another, tens of metres apart — it shows you both and lets you say which is right. If the search comes back empty, the name you already had is left exactly as it was. From **@hubcasale** (#162).
- **Verify keys**, in Settings → Charging stations: a live check of your Open Charge Map and TomTom keys against the real service, before saving them — so a mistyped key is caught there and then instead of quietly returning nothing for weeks.
- **The estimated range at 100 %**, under the current range on the Overview: what this battery would give you on a full charge at your current consumption. From **@domevite** (#161).
- **A map marker threshold**, in Settings → Charging stations. By default every station you've used gets a marker, including a charger you stopped at once on a trip — which is precisely the stop you're least likely to remember unaided. If your map is crowded with one-offs, raise it to show only the places you return to. From **@hubcasale** (#162).

### Changed
- **When two sources describe the same charging station, the one with more to say now wins.** Before, whichever happened to be nearest became the label — an artifact of the order the sources answered in, not a measure of what they knew — so a bare pin could hide a source that had the sockets, the power and the address. The richer entry is now the one kept, and the other fills in whatever fields it's still missing. From **@hubcasale** (#162).

### For the desktop app
Groundwork for the standalone Mac app, which packages Mate for people who run neither Home Assistant nor Docker. **None of this appears on Home Assistant or Docker** — it's keyed on a variable only the app's launcher sets.
- The update badge no longer sends you to GitHub: the app fetches the new version by itself on the next launch, so it says *"updates on the next restart"* instead. It turns red, with a real download link, only for an update the app has **refused** because it is too old to run it — the one case that never arrives on its own.
- The app's own version is shown next to Mate's (`v2.9.0 · D1.0.0`), in the sidebar and in the diagnostics bundle, so a report from the app can say which build produced it.
- A dismissible notice that **the app records only while it is open**: anything the car does after you close it is not saved, and cannot be filled in afterwards — the cloud keeps no history to replay.

## [2.8.9] — 2026-07-24

### Changed
- **The altitude on the trip chart is a plain line again, like SoC and speed.** It was drawn as a filled area — a soft gradient when the profile arrived in 2.7.0, then a flat opaque slab in 2.8.3 — and the fill had become the loudest thing on the chart, a brown mass covering the two lines the chart exists to show. The terrain reads perfectly well as an outline, and it now has the same weight as the other two series, so the plot is about the drive again with the ground as context rather than the subject. Nothing else changes: the dashed segments across signal drop-outs stay, and so do the gain/loss figures. Raised by **@pdifeo** (#159).

## [2.8.8] — 2026-07-24

### Fixed
- **Cars west of Greenwich are no longer moved out to sea by the Refresh button.** A B10 in Portugal appeared on the map off the coast of Sardinia, and went back there every time its owner refreshed. The cloud sends each coordinate twice — once with its sign, once as a bare magnitude — and Mate has two places that read it: the poller, which picks the signed pair and got this right, and the web, which keeps its own copy of that parsing for the write that follows a **command** or the **Refresh** button. That second copy read the magnitude, so the newest position was filed at *plus* 9.14° instead of *minus* 9.14°. Since the map shows the newest position, one refresh was enough to send the car to Italy. The stored history was never wrong — only the last row was — so nothing needs repairing: the next refresh puts the marker back. This is the same fault as the UK car plotted in the North Sea (#30), fixed then in the poller alone; nothing east of Greenwich could ever show it, which is why the second copy went unnoticed. It mirrored the **latitude** too, so cars in the southern hemisphere were thrown into the opposite one. Reported with a diagnostics bundle by **@Andreexylus** (#158).

### Changed
- **The diagnostics bundle now describes the shape of the GPS data, without any coordinates.** Every coordinate signal is stripped from a bundle to protect your address — but that also made the bug above invisible to triage, since nothing in the bundle could say whether a car even sends the signed pair. Bundles now list which coordinate signals arrive and which hemisphere Mate has learned, and no position. The `/api/debug/signals` endpoint — written specifically to diagnose this class of problem — was likewise not listing the signed signals at all, and now shows them alongside what Mate would actually store.

## [2.8.7] — 2026-07-24

### Fixed
- **"Fully charged" no longer appears while a range-extender is still charging.** A REEV can raise its own *charge complete* flag with the battery at 23 % and the limit set to 90 %, switching it on and off part-way through a charge — and Mate was repeating that faithfully, so the Overview and the Charges page announced a finished charge while the car was visibly filling. Mate now checks that claim against the battery before showing it: if the charge is nowhere near the point the car was told to stop at, the flag is ignored. It stays deliberately forgiving — a charge that stops a few percent short of the limit is still "complete" — and it only ever applies when Mate actually knows the limit, so a legitimate flag can't be suppressed for want of a reference. **Only range-extenders are affected**; fully-electric cars report this correctly and are left exactly as they are. Spotted by **@michapr** alongside the charge-detection issue in the same report.

## [2.8.6] — 2026-07-24

### Fixed
- **A range-extender charging at home is finally recorded.** On a REEV, a home AC charge could go completely unlogged — no session at all, so the energy and cost simply never appeared. The reason is that these cars report neither of the two things Mate looks for: the cable state stays at "connected" instead of switching to "charging", and the pack current reads about a tenth of an amp instead of a real charge current. A tester's diagnostics settled it beyond doubt — across fifteen days the poller never once entered the charging state, while the battery visibly climbed the whole time. Mate now takes the climb itself as the evidence: **on a REEV, parked with the cable in and the battery genuinely rising, that's a charge**, whatever the cable and current claim. The rise is measured cumulatively from the moment you plug in rather than between two readings, because on these cars the battery moves one 0.1 % step at a time — which is also the sensor's own resolution, so comparing consecutive readings would either miss the charge or fire on noise. A charge that's merely *scheduled* and waiting for its slot, with the battery flat, still correctly does **not** open a session, and neither does driving on the generator. **Fully-electric cars are untouched** — the new path is gated strictly on the car reporting a fuel tank, so a BEV can never reach it. Thanks to **@michapr**, whose diagnostics and patient re-testing pinned this down.

## [2.8.5] — 2026-07-23

### Changed
- **The OTA check now records what it saw**, so a diagnostics bundle can explain *why* the Overview shows "None" for updates. Leapmotor has no OTA-status signal — nothing the car reports says an update is pending — so Mate can only scan your account's message inbox for an "update available" notice. That meant a bare "None" hid three very different situations that looked identical: the inbox is empty (the car downloaded the update without ever posting a message), there are messages but none is an update, or the inbox couldn't be read at all for your account/region. The poller now logs which of the three it is (and warns, rather than staying silent, when the message endpoint doesn't answer). No behaviour change to detection itself — this only makes the existing check observable. Prompted by **@ghuaywen-ai** (#156).

## [2.8.4] — 2026-07-22

### Fixed
- **A range-extender charging slowly at home is logged again.** On the B10 and C10 REEV, a slow AC charge at home could go completely unrecorded — no session on the Charges page, even though the car was plugged in and filling. Two things about how these cars report a charge defeated the detector, both confirmed from real diagnostic bundles going back weeks: the pack current reads about **zero** the whole time (the on-board charger feeds the battery by a path that sensor doesn't measure, so the usual "is current flowing?" test never triggered), and the cable-state signal **flickers** between three values mid-charge — one of which Mate read as "unplugged", so it kept closing and reopening the session and shredding one charge into a string of tiny fragments, each then discarded as empty. On a genuinely slow charge every fragment was empty and the whole thing vanished. Mate now trusts the cable's own "charging" state together with the ticking-down remaining time when the current is zero, and treats that third cable state as still-connected so the flicker no longer splits the session. Driving and a scheduled charge waiting for its slot are unaffected — both are still correctly *not* a charge. Thanks to **@michapr** and **@ebagnoli**, whose bundles made the signature clear. Full-electric cars are untouched: the new path only opens for the exact zero-current signature a REEV produces.

## [2.8.3] — 2026-07-21

### Fixed
- **The terrain no longer vanishes where the car lost signal.** On a trip with a telemetry drop-out, the profile chart opened a hole: battery and speed went blank — correctly, since nobody knows what they were — but the altitude line went blank with them, and the ground under the car hadn't stopped existing. The relief now continues across the gap as a **dashed line**, drawn straight between the last and the next known altitude, so a climb reads as one continuous silhouette instead of two disconnected halves. The dash is the point: that stretch is reconstructed, not measured. Battery and speed still stop at the edge of the gap, exactly as before. The altitude band is also filled in a flat tone now rather than a fade, which makes the shape of the ground easier to read at a glance. Thanks to **[@hubcasale](https://github.com/hubcasale)**, whose PR #155 this is.

## [2.8.2] — 2026-07-21

### Security
- **A page on another website can no longer make Mate do things.** Mate's API can unlock the doors, and standalone Docker ships with no password — the `MATE_AUTH_PASSWORD` variable is opt-in and, realistically, almost nobody sets it. That combination meant the attacker who mattered was never someone on your network: it was any web page open in *your* browser, which is already inside it. A page could quietly submit a request to Mate on your home address, and the browser would deliver it — the reply is unreadable to the attacker, but the car has already unlocked. **Mate now refuses any request that changes something and declares it came from somewhere else.** A request with no origin at all still works, because that's `curl`, a script or a Home Assistant automation, and browsers always declare an origin on the attack this blocks. Mate also **refuses to be embedded in another page**, which closes the other half of the same trick — a hidden frame with a button lined up over *Unlock*, where the click genuinely happens inside Mate and no origin check could ever see it. Running as a Home Assistant add-on is unaffected throughout: ingress already authenticates every request, and it deliberately embeds the panel. If you reach Mate through a reverse proxy that rewrites the host, `MATE_TRUSTED_ORIGINS` lets you list the address it should accept.
- **The password can now be switched on from the page.** The login has existed for months, but turning it on meant editing a compose file and restarting a container — so the protection existed and nobody had it. *Settings → Access* now takes a password directly, and a banner says plainly when there isn't one, because the stake is physical rather than abstract: anything on your network can open Mate, and Mate can open your car. The password is stored as a salted hash and never in clear text, so a backup or a diagnostics bundle carries nothing usable. `MATE_AUTH_PASSWORD` keeps working exactly as before and takes precedence — if it's set, the page says so instead of quietly ignoring what you type. The banner can be dismissed for good; someone running Mate on localhost only, or behind their own authentication, isn't wrong.
- **The login now has a brake.** One shared password and no account to lock meant anything on the network could try a wordlist as fast as it could send requests. Five wrong attempts from the same address now close the door for five minutes, and the page tells you *that's* what happened rather than showing the same "wrong password" — because the person who hits it is usually the owner.

### Added
- **Charging stations on the map, and a per-station view of your charges.** Every completed charge is now grouped by where it physically happened, and the map draws each spot as its own marker: the station's name where Mate has resolved one, how many times you've charged there, the energy and the cost, the most recent sessions, and a link straight through to the Charges page filtered to just that station. Home charging stays off this layer — a wallbox isn't a public charger, and being nearly everyone's most-visited spot it would otherwise sit on the map as one enormous unnamed bubble. A station you used only once still gets a marker: that's the charger you stopped at on a trip, which is exactly the one you're least likely to remember unaided.

### Fixed
- **Mate no longer picks the wrong vehicle in three places.** An internal lookup asked the database for "a vehicle" without saying which one, and SQLite is free to answer in whichever order it finds convenient — in practice, by chassis number. Two of those three places *write*: the sleep-time charge reconstruction (which can add charges to your history) and the telemetry saved right after a command. Nobody could have hit this yet, because it needs a second car, but it was found by building exactly that case and it's closed now.
- **The charge list's session counts are translated.** The year, month and day rows of the Charges tree spelled "sessions" in English regardless of your language, and with an English plural rule. All three now follow the interface language, singular and plural.
- **The demo no longer errors on the vehicle page.** The bundled sample data predated the climate card, so the vehicle status panel returned an error for anyone trying Mate without a car.

### Credits
- The charging-station map layer is **[@hubcasale](https://github.com/hubcasale)**'s work in PR #153 — the clustering, the map layer, the popup and the per-station filter on the Charges page are his. He also turned a review round on it in a morning, including keying the home-charging exclusion to the wallbox position Mate learns by itself so it works on a fresh install with nothing tagged. Thank you 🙏

## [2.8.1] — 2026-07-21

### Fixed
- **A public charge is no longer mistaken for a home/wallbox session.** A home wallbox stays reachable while you charge somewhere else entirely, so its energy counter still answered — and Mate attributed that reading to the away-from-home charge. Two things went wrong: the public charge was excluded from the 📍 charging-station name lookup (it looked like it happened on your wallbox), and if you measure the wallbox through an energy meter that also sees its standby draw, the counter slowly *creeps* while the car is away — enough, at slower poll rates, to be counted as real home energy and shift that charge's cost. Mate now learns where your wallbox is (from the charges where it actually delivered energy) and simply doesn't attribute its counter to a charge it knows happened far away. It stays deliberately cautious: a charge with no GPS fix (a garage or underground box), or any install that hasn't seen enough home charges yet, is attributed exactly as before — so a genuine home charge is never dropped. Thanks to **@hubcasale** for surfacing the mis-attribution in #152.
- **The diagnostics bundle now reproduces your battery page.** The standby-drain section of the downloadable diagnostics recomputed with the *default* minimum-parking-duration instead of the one you set under *Settings → Advanced*, so anyone who had raised that threshold got a bundle that listed more (and shorter) parked windows than their own chart shows — a discrepancy that could send troubleshooting down the wrong path. It now honours your setting and prints it in the header. Thanks to **@riri19** (#154), whose bundle made the mismatch visible.

## [2.8.0] — 2026-07-20

### Added
- **Set the car's charging plan from Home Assistant.** Until now the charge schedule — start time, end time, target level, which days — could only be set inside Mate's own interface, which left it invisible to automations. Mate now publishes a **Charge Schedule** entity over MQTT that accepts a small JSON plan, e.g. `{"start":"23:00","stop":"07:00","soc":90,"active":true}`, so you can drive it from an automation: charge when electricity is cheapest, when your solar is producing, or on any condition Home Assistant can express. **Every field is optional, and anything you leave out keeps its current value** — an automation can send just `{"start":"23:00"}` and the rest of your plan stays exactly as it was. Bad input (malformed JSON, an impossible time, a target outside 50–100 %) is refused outright rather than half-applied, and the entity reports back the plan that was actually written so Home Assistant doesn't show an empty box. Thanks to **@chengler**, who worked out the payloads on his own T03 and shared them in #151 — the tests replay his exact sequence.

## [2.7.1] — 2026-07-20

### Fixed
- **Changing the charge limit from Home Assistant no longer wipes a "start time only" charging plan.** If your car has an enabled charge plan with just a start time and no weekday selection, moving the *Charge Limit* number in Home Assistant silently **disabled that plan and reset its start to 00:00** — so the car simply stopped charging overnight, with nothing visibly going wrong to explain it. This is the same upstream quirk that was fixed for the web UI back in v2.5.8 (the library's charge-limit helper falls back to an all-defaults, schedule-disabled payload whenever the cloud omits the weekday mask — which is exactly what it does for start-time-only plans), but the MQTT path was still calling that helper directly. It now performs the same read-modify-write the web UI does: **only the target SoC changes**, while the plan's enabled state, start/end window, days, circulation and recharge all round-trip untouched. Owners whose plan has weekdays selected were never affected. Thanks to @chengler, whose charge-scheduler experiments (#151) led to this being spotted.

## [2.7.0] — 2026-07-20

### Added
- **Elevation profile and outside temperature for every trip.** The Leapmotor cloud reports neither altitude nor an outside-temperature signal — only latitude/longitude and the cabin temperature. Mate now looks each finished trip's GPS track up against [Open-Meteo](https://open-meteo.com) (free, keyless, no account) shortly after the trip ends, and the trip detail gains three things: an **altitude line** on the *SoC & speed* chart — a topographic cross-section drawn under your telemetry — the trip's total **elevation gain and loss** (↑ climbed / ↓ descended), and the **outside temperature at the departure point and at the arrival point**. The temperature is deliberately *not* an average: it's read at each point's own place and hour, so a valley-to-pass climb shows the real drop instead of hiding it. Together these explain a good part of a trip's consumption — a climb costs energy, cold costs range. It runs quietly in the background (the same render-triggered sweep as the other post-trip enrichments), one lookup per trip; a trip whose lookup fails simply shows "—" and retries on the next sweep, up to a small ceiling, and trips recorded before this feature existed get a **Calculate elevation** button right on the page. Only the trip's GPS points ever leave the device, and the whole thing can be switched off in *Settings*. Elevation follows your measurement system (m / ft), and the chart's altitude line only appears on trips that have been enriched.

### Credits
- The elevation groundwork comes from **[@hubcasale](https://github.com/hubcasale)**'s work in PR #147: the post-trip enrichment sweep, the per-point altitude storage with read-time interpolation, the chart's third series and the recalculate button are his design. This release switches the lookup to **Open-Meteo** — ~300× the free quota of Open-Elevation, finer 90 m terrain data, and it can return the temperature in the same trip — and adds the outside temperature on top. Thank you 🙏

## [2.6.1] — 2026-07-18

### Fixed
- **The car picture now updates when the colour changes.** Mate downloads your car's image (with its real model and colour) from the Leapmotor cloud, but it only did so once and then cached it forever — so a car whose colour was still provisional in the cloud at first setup (typical of a brand-new car) could stay showing the wrong colour indefinitely. Mate now re-downloads the picture **once after each restart** (so every Mate update refreshes it), while still serving the cached image instantly the rest of the time and keeping it if the cloud is briefly unreachable. Thanks to @TripelJ for spotting it (a new B10 that reads purple in the cloud and the official app but showed white in Mate) — #143.
- **The T03 no longer shows controls it doesn't have.** The T03 has no heated or ventilated seats and no heated steering wheel, but Mate was still exposing those entities and buttons (they come from capabilities the car's firmware declares but the European T03 doesn't physically have — the same quirk as its climate). They're now hidden on the T03, in the web UI **and** in Home Assistant, leaving only what it actually has (e.g. the heated mirrors). Cabin and battery temperature are unaffected. Verified against the official European spec. Thanks to @staffhotel-beep for the report (#144).
- **The charge-schedule view is simplified for cars that only do a simple plan.** The Scheduling page offered an end-time and day-of-week picker, but a car like the T03 only supports a start time + target charge level — so those extra fields did nothing there. Mate now shows them only on cars that declare weekly/cyclic charging, leaving the T03 with just start time and target SoC. What the car receives is unchanged. Thanks to @chengler (#146).

## [2.6.0] — 2026-07-18

### Added
- **Pick your time zone.** Trips, charges and reports are shown in local time — but that time used to come only from wherever Mate happened to be running, so a bare Docker container (or a Home Assistant whose zone Mate couldn't read) showed everything in UTC, hours off. Settings → *Language & Currency* now has a **Time zone** selector: leave it on **Automatic** to keep using the container/HA zone exactly as before, or pick your own from the full list of world zones. It's display-only — your stored data always stays UTC — and it applies everywhere at once, so every past trip and charge re-aligns the moment you set it. Daylight-saving is handled automatically. Time-of-use charges are priced in the selected zone from here on (existing charges keep the cost they were recorded with, as always). Thanks to @dsbloomer for the request (#145).

## [2.5.18] — 2026-07-17

### Fixed
- **The "Unlock Charge Cable" button is hidden on cars that can't do it.** The T03 can't release the charge cable remotely — the official app has no such option either — but Mate showed the button anyway, where it simply did nothing. Mate now gates command buttons on the abilities the car itself declares: any model that doesn't report the unlock-cable capability no longer shows the button, in the web UI and in Home Assistant (MQTT), and the command is refused server-side with a clear "not supported on this model" instead of being bounced off the car. This is model-blind — it reads what each car declares about itself, so it also covers other models present and future, with no per-model list to maintain. Climate is deliberately left out of this (the T03 under-declares its climate abilities yet the A/C works — #67), so nothing there changes. Thanks to @chengler for the report and the diagnostics (#142).

## [2.5.17] — 2026-07-17

### Fixed
- **The setup wizard no longer offers a range-extender battery.** Mate supports **battery-electric cars only**, but its first-run wizard was still listing a range-extender pack among the battery variants for the B10 and the C10 — visible to anyone setting up either model, not only to range-extender owners, since the cloud reports just the model name. Choosing it configured the car as something Mate has no support for, and the energy figures that followed could not add up. The wizard now offers the battery-electric packs only, and the page no longer carries the variant at all. **Existing installs are untouched**: this is the first-run screen, and no configured car is changed. Range-extender owners are served by the separate BetaTester build, which is where that support is being developed. Thanks to @pdifeo for the question that surfaced it (#141).

### Changed
- **The wizard's battery list now comes from one place.** The list existed twice — once in the server and once hardcoded in the setup page — and the two copies had already drifted apart (gross vs usable figures for the same B10 packs). The page is now rendered from the server's list, so the two can no longer disagree.

## [2.5.16] — 2026-07-16

### Fixed
- **Trips no longer record phantom data when the car goes off-grid.** When the car can't reach Leapmotor's cloud — a 4G dead zone, or the eSIM re-registering onto a foreign network after a border crossing — the cloud doesn't report the outage: it keeps re-serving the **last frame it received**, unchanged, for as long as the car stays away. Mate was recording each of those repeats as a real sample, which invented data: a speed plateau frozen at the last reading, dozens of GPS points stacked on the same spot, and an **average speed inflated** by them. Mate now recognises a re-served frame (the frame's own timestamp doesn't change) and, while driving, records nothing for it — so the trip chart shows an honest **gap** for the stretch the car was unreachable, instead of a confident flat line through minutes that never happened. Distance and efficiency were already correct and are unchanged: the odometer catches up the moment the link returns. Parked cars are unaffected — a sleeping car legitimately freezes for hours. Thanks to @Wartopia for the report and the diagnostics that pinned it down (#128).

## [2.5.15] — 2026-07-15

### Added
- **Portuguese: the Maintenance page is now translated too.** The maintenance schedule — service items, categories and the due/overdue wording — was the last part of the interface still showing in English for Portuguese users, and is now fully in European Portuguese. Thanks to @danielvilhena for the contribution (#139).

## [2.5.14] — 2026-07-15

### Added
- **Windows and sunshade/roof commands over MQTT.** Open/close the windows (a quick vent to 20%) and the sunshade roof directly from Home Assistant — four new buttons that until now existed only in the web UI. Ideal for automations, e.g. auto-close the sunshade when the car arrives home. Like every physical command, the car only acts on them while parked. Thanks to @SgtMajorSnipr for the request (#138).

## [2.5.13] — 2026-07-15

### Added
- **Portuguese (Portugal) translation.** Mate is now available in European Portuguese (`pt-PT`) — a complete translation of the whole interface, selectable in Settings and during first-time setup. Thanks to @danielvilhena for the contribution (#137).

## [2.5.12] — 2026-07-13

### Added
- **REEV: fuel cost — log your refuels and see what each engine-on trip cost (beta).** A new **Rifornimenti** page lets you record each refuel (litres + price per litre, *or* the total paid — the other is computed). Mate keeps a **weighted-average price** of the fuel currently in the tank — a big cheap fill drags it down, a small pricey top-up nudges it up, exactly like a tank that's "part bought at price X, part at price Y" — and prices each engine-on trip's fuel at the average in effect **at the time of that trip**. The per-trip dual-energy view now shows the **fuel cost** next to the litres, and the page shows the live tank state (level from the car, average €/L, estimated value). Prices are shown to 3 decimals with the UI's decimal separator. Visible on the REEV research build only, and only on range-extender cars; battery-electric cars and the official build are unaffected.

## [2.5.11] — 2026-07-12

### Changed
- **REEV: regen is no longer shown.** Mate infers regen from "battery charging while driving" — but on a range-extender that is indistinguishable from the **generator** charging the battery, and the cloud API exposes no dedicated regen signal to tell the two apart. Rather than display an inflated, misleading number, the regen field is now hidden on range-extender cars everywhere it appeared (trip detail, statistics, trip list, monthly report). **Battery-electric cars are unaffected.**

### Added
- **REEV: per-trip dual-energy view, and the electric side now comes only from the cloud (beta).** An engine-on range-extender trip now shows both of its energies side by side — the **electric** consumption from the car's own metered cloud figure (getEC) and the **fuel** used (L + L/100 km). The electric side is sourced **only from getEC, never from the battery SoC**: on a REEV the generator recharges the pack *while you drive*, so the net SoC change isn't the motor's consumption — it can even go *up*. getEC counts real consumption regardless of where it comes from, so it stays correct even then; a trip that ends fuller than it started now reads **"battery recharged by generator"** instead of a nonsensical negative number. The REEV page also gains the absolute electric **kWh over the last 7 days** (driving / climate / other) next to the fuel litres. Visible on the REEV research build; battery-electric cars are unaffected. Thanks to @gm27271 and @michapr for the reports and reasoning (MateBetaTesterOnly #10).

## [2.5.10] — 2026-07-11

### Changed
- **Translations moved to per-language JSON files (internal cleanup — no user-facing change).** The UI strings used to live in a single ~4,500-line Python module; they now sit in `web/locales/{en,it,fr,de,pl}.json`, loaded by a small 56-line `i18n` loader with the exact same behaviour. This makes adding or correcting a translation a simple JSON edit (and opens the door to community translations) without touching code. Every string was carried over verbatim — the app looks and behaves identically in all five languages.

## [2.5.9] — 2026-07-11

### Fixed
- **REEV: a trip's electric kWh/100 km is no longer shown when the range-extender ran.** On a range-extender car the generator recharges the battery *while you drive*, so the trip's net SoC change isn't the motor's traction energy — the old per-trip electricity figure (from SoC delta, or from getEC spread over the full distance) came out diluted or near-zero (e.g. 0.5 kWh/100 km where the car itself reported ~19). Mate now **withholds** that figure whenever the extender ran during the trip — at trip close, in the getEC conversion, in merged-trip stats, and via a one-time cleanup of already-recorded trips — so no misleading number shows and no average is polluted. **Pure-electric REEV trips** (fuel level flat) keep a valid figure, and **battery-electric cars are entirely unaffected** (they have no fuel signal). The trip still shows its **fuel** L/100 km (measured over the generator-on distance). Thanks to @gm27271 for the sharp report (MateBetaTesterOnly #10).

## [2.5.8] — 2026-07-10

### Fixed
- **Setting the charge limit no longer wipes a start-time-only charge schedule.** If you had scheduled charging enabled with only a start time (no end time and no specific days), changing the charge limit could silently **disable the schedule and reset its start time to 00:00**. The cause was in the underlying library's read-modify-write: it keyed on the day mask, and the cloud omits that mask for a start-time-only plan, so it fell back to an all-defaults branch. Mate now round-trips the current plan and changes **only** the target SoC — the schedule's enabled state, start/end window and days are preserved (leapmotor-api #18). Schedules that had specific days set were never affected.

## [2.5.7] — 2026-07-10

### Changed
- **Diagnostics bundle is now a ZIP and carries days of logs, not minutes.** The downloadable diagnostics used to include only the last ~300 log lines (≈50 minutes of driving) as a plain-text file — too short to cover a long trip. It now bundles the **full retained poller/web logs** (the active file plus its rotated backups) and ships them **zipped** (plain text compresses ~10×, so the attachment stays small). The poller log also keeps a wider window on disk (≈3 days of continuous driving, longer when parked), so when someone reports a problem a few days later their bundle still spans the whole run-up. VIN and credentials are still masked and GPS is still stripped — the bundle stays safe to share publicly.

### Added
- **Each poll now logs the age of the cloud's telemetry frame.** Every poller status line carries a `Frame age` field (and the odometer) — how old the frame the cloud actually served is. Fresh data reads a few seconds; if the car enters a connectivity dead zone and the cloud keeps re-serving its last frame, the age climbs without bound. That is the signal that tells a *stale* reading apart from the car *genuinely* being stopped — making problems like a trip that stays "in progress" after a border crossing (#128) diagnosable from a single bundle.

## [2.5.6] — 2026-07-08

### Fixed
- **Battery health (SoH) — ignore charges that started from a near-empty battery.** A charge begun at a very low state of charge shows up as an isolated low dip in the health graph: near empty, the BMS over-reports the SoC rise, so "energy ÷ SoC gain" under-estimates the pack capacity. Charges starting below 15% are now drawn on the chart in violet but **excluded from the health figure and its average** — the same treatment already given to cold charges and BMS-recalibration jumps. This adds the *bottom* guard to complement the existing *top* one (charges ending near 100%, where the BMS is most accurate, already weigh most), so the trend stays honest at both ends of the LFP curve. Thanks to @riri19 for the precise report (#125).

## [2.5.5] — 2026-07-08

### Fixed
- **REEV per-trip fuel consumption (L/100 km) now matches the car.** On a mixed drive — part on the range-extender, part pure-electric — the litres were spread over the *whole* trip distance, which under-reported the figure. It's now measured over only the distance driven **while the generator was actually running** (the electric kilometres, and any fuel burned while stationary to charge the battery, are excluded), so the per-trip value, the trips list, and the REEV summary all line up with what the car itself shows. The trip card also shows the generator-on distance the figure is based on. Research / BetaTester builds on a REEV vehicle only — nothing changes for BEVs or the normal add-on.

## [2.5.4] — 2026-07-08

### Added
- **Restore a database backup — carry ALL your data to a new install.** Settings → *Export / Backup* now has a **Restore database** option next to the existing backup download: export your database, reinstall the add-on, sign in, then upload that backup to bring **everything** back — trips, charges, positions, research history, settings — without losing a single row (verified end-to-end). The login you just entered is kept: the backup's own encrypted secrets can't be read on a different install, so you sign in once and Restore preserves that. The backup line also now shows what it contains (trips · charges · positions · size), so you can be sure it's complete before downloading. Foreign or corrupt files are refused without touching your data.

## [2.5.3] — 2026-07-08

### Changed
- **BetaTester channel now updates like any normal add-on.** The data-collection BetaTester add-on was pinned to a fixed `beta` version, so Home Assistant never offered it an update. It now runs the **exact same image and version number** as the regular release and enables its extra data capture through an add-on option instead — so it shows a real version, updates with the normal button, and stays in lockstep with every release. **No change for the normal add-on** (the option is absent there, so nothing is enabled).

## [2.5.2] — 2026-07-08

### Fixed
- **A charge is now tracked from when charging actually starts, not from when the cable is plugged in.** Mate used to open a charging session the moment the cable was connected. With a scheduled (deferred) charge the cable can stay plugged in for hours before any current flows, so the session started counting too early. It now begins only when real charging current is present — the car reporting "charging", or ≥ 2 A — which is the behaviour the state machine documented all along. This removes the empty session at plug-in and, if you meter AC energy with a wallbox counter or a Shelly, keeps the standby draw during those idle plugged-in hours out of the charge's energy and cost (the meter baseline is now taken when charging begins, so those hours fall below it). The cable still ends a trip immediately on plug-in and still keeps one physical charge from splitting across brief current dips.

## [2.5.1] — 2026-07-07

### Added
- **Trigger commands from scripts, Shortcuts or Home Assistant, and get a JSON reply.** Send `Accept: application/json` to `POST /api/command/{name}` and the endpoint answers with a structured result — `{"ok": true, "status": "done"}` on success, or `{"ok": false, "error": ..., "blocked"/"cooldown"/"retry_in": ...}` — instead of the HTML the web UI uses. So an iOS Shortcut, a smartwatch button, a shell script or a Home Assistant `rest_command` can fire a command and actually know whether it worked, was blocked while driving, or hit the anti-spam cooldown. The browser path is unchanged — JSON only when explicitly requested. Thanks @irek for the idea.

### Fixed
- **The Monthly report now shows the average consumption for a first, partial month too (#121, thanks @Wartopia).** When you install Mate part-way through a month, the report's window still started on the 1st — but the cloud energy figure (getEC) covers those earlier days too (the car reports to the cloud on its own), while Mate only has trips from when it was installed. Pairing the two would give a wrong average, so the report used to leave it blank ("—"). It now aligns the window to your first recorded trip, so the cloud energy and your trip distance cover the same period and the real average shows — the same logic an established month already used. Established months are unchanged.

## [2.5.0] — 2026-07-07

### Added
- **Range-extender (REEV) support is taking shape in the BetaTester build.** On the BetaTester data-collection build, a range-extender car now gets a **dual-energy Overview** — battery *and* fuel tank side by side (fuel level, range on fuel, and the combined battery+fuel range) — and every **engine-on trip is flagged with its petrol consumption** (litres and L/100 km), so REEV testers can check the new UI against their own car while we finish REEV support. It's exclusive to the **BetaTester build** and to **REEV cars**: a normal Mate — and any battery-only (BEV) car — shows nothing new.

## [2.4.1] — 2026-07-07

### Fixed
- **The Charges page now shows your full history, not just the last 50 (#67, thanks @rossiadobe).** After importing a long charge history from CSV, the list appeared to stop part-way (older charges looked missing) — the grouped Charges view was capped at the 50 most recent charges. The charges were always safely in the database (the CSV export and the monthly report already showed every one); only the on-screen list was truncated. It now loads the complete history, grouped by year / month / day as before.

## [2.4.0] — 2026-07-06

### Added
- **Mark a home charge as "Free" (#120, thanks @Wartopia).** A home charge that cost you nothing — self-produced solar, or any free top-up at home — can now be flagged **🆓 Free** with a toggle right next to its **Home** badge. The charge **stays under Home**, so it still counts on the Home side of the Home-vs-Public breakdown instead of being lumped into "Public", but its cost is pinned to **0** and protected from any later recompute. It's a manual flag by design: Mate only sees what the wallbox/battery report and **can't tell solar from grid** (there's no metering behind your meter — a session is usually a mix), so whether a charge was "free" is your call, not a measurement. Free charging *away* from home stays the existing **FREE** type; this toggle is Home-only, and switching a charge to any other type clears it.
- **Default drive mode and One-Pedal for new trips (#119, thanks @Wartopia).** Drive mode (Comfort/Normal/Sport) and One-Pedal are never reported by the Leapmotor cloud (verified on-car) and can't be inferred from speed, so they can only be tagged by hand. If you always drive the same way, you can now set your defaults once under **Settings → Trip defaults**, and every **new** trip starts pre-filled instead of "not set" — no more re-tagging each trip. Applies to new trips only (existing ones are untouched), you can still override any single trip afterwards, and the factory default stays "not set" so nothing changes unless you choose one.

## [2.3.0] — 2026-07-05

### Added
- **Trips driven while the car was offline are now recovered (#118, thanks @riri19).** When your car's modem goes silent during a drive — no live data reaches the cloud, so the trip used to be lost entirely — Mate now rebuilds it from the odometer jump the moment the car comes back online, exactly like it already does for charges missed while the car was asleep. The reconstructed trip **counts in your statistics** (distance, energy, consumption) and is clearly marked **"auto-detected" (✨)**.

  **Please note the limits:**
  - **From now on only.** It catches offline trips from this update onward; trips already lost *before* it are not recovered.
  - **No map/route.** The car sent nothing during the drive, so there's no GPS track — the trip shows distance and consumption but **no route on the map** ("Route not available").
  - **Approximate on unstable connections.** On a flaky car↔cloud link the **duration may be blank** when the offline gap is much longer than the drive, and **several drives inside one long offline gap merge into a single trip** (the totals stay correct, the per-trip split doesn't).
  - **Estimate, not official.** Energy/consumption come from the battery-level drop, not the cloud's official per-trip figure — a reconstructed trip **can't be "converted to official data"** (the cloud never saw it).

## [2.2.0] — 2026-07-05

### Internal — no change if you have a single car
- **Multi-vehicle groundwork.** Mate now stores battery capacity **per vehicle** and scopes every page's data (trips, charges, statistics, battery health, maps, diagnostics) to a specific car. This lets a future release show and manage more than one vehicle on the same account without ever mixing their data. On a single-car setup it is completely invisible: the database migration is a no-op that leaves your existing trips and charges untouched, and every screen shows exactly what it did before — verified end-to-end against the full data set. A one-time safety pass also guarantees no historical row can be hidden by the new scoping.
- **REEV research capture (beta build only).** The beta/research build additionally records the cloud's per-100 km electric **and fuel** consumption split, to validate range-extender support from real REEV cars. Inert in the normal build.

## [2.1.9] — 2026-07-04

### Fixed
- **Scheduled preparation: saving no longer fails with "Request failed"** (#116, thanks @riri19). A one-time ("once") prepare-car appointment that has already fired used to linger in the list, and since saving *any* schedule re-sends the **whole** list to the car (the cloud only supports full-list replacement), a single spent "once" entry made the car reject the entire batch — so no schedule could be saved or deleted. Mate now drops already-fired "once" appointments from the write: saving works again, and those dead entries get cleared from the car in the process. Recurring (weekday) schedules are untouched — they were never the problem, and the car re-anchors them on its own. These stale "once" entries are typically left over from the official app, since schedules live on the shared Leapmotor cloud.

## [2.1.8] — 2026-07-04

### Changed
- **T03: Mate now shows only what the car actually has.** The T03 has no ventilated seats and no one-touch "Prepare car", so those controls — the seat-ventilation tiles and the Prepare-car page/menu entry — are now hidden on a T03 instead of sitting there inert. It's driven by a small per-model table, so a future model can be tuned the same way in one line. Climate is deliberately left untouched (the T03's climate is handled empirically, #67). **B10/C10/B05 are unchanged.**

### Fixed
- **Wallbox: no more blank, label-less tile** (#114, thanks @Wartopia). On the Wallbox page the "max current" control drew an empty "—" with no label when the wallbox exposes no controllable current entity (e.g. PlugChoice), which read as a broken box. It's now simply hidden in that case; the slider is unchanged for wallboxes that do expose a controllable current. (The other tiles that look empty during a live first session — Wallbox/Battery kWh, Efficiency, Car-vs-Wallbox — are lifetime/comparison values that fill in once the charge completes.)

## [2.1.7] — 2026-07-03

### Fixed
- **Maintenance: the km bars now start from the real delivery odometer** (#112, thanks @Wartopia). The delivery editor gained an **"Odometer at delivery (km)"** field — set it (0 for a car bought new) so a first-service progress bar reflects the car's actual odometer, not just the kilometres driven since Mate was installed.

## [2.1.6] — 2026-07-03

### Added
- **CSV charge import now takes start/end SoC and an end time** (#67, thanks @rossiadobe). The import template has three new **optional** columns — `start_soc`, `end_soc` and `end` (end date/time) — so imported historical charges show their **SoC gain and duration** just like live ones. Still SoH-safe: manual charges stay out of the battery-health estimate.
- **Wallbox "max available" can be a fixed value** (#111, thanks @Wartopia). If your wallbox exposes no sensor for its maximum power/current, you can now type a **static value** (kW or A) in the wallbox mapping and the live tile fills from it. A mapped sensor still wins; display-only, it never affects cost.
- **T03: an experimental "A/C off" test page** (#67). The T03's remote A/C-off is unsolved across the whole ecosystem, so — **for T03 owners only** — a card in Settings opens a page to try candidate off commands (plus fan/heat/recirculation probes) on the real car and report what works. Temporary; it'll be removed once the command is found.

## [2.1.5] — 2026-07-03

### Added
- **Bulk-import past charges from a CSV** (idea from #111, thanks @Wartopia). The Charges page now has an **Import from CSV** section next to "Add a past charge": download an empty, self-documenting template, fill one row per charge (date, energy, optional cost, AC/DC) in Excel/Numbers/any editor, and upload it. Every line is validated strictly — a bad date, future date, non-numeric or absurd energy, negative cost or unknown type is rejected and reported with its line number, while the good lines still import — so one typo never blocks the file and nothing dirty reaches the database. Both `,`+`.` (US) and `;`+`,` (European Excel) CSV formats are auto-detected.
- **Diagnostics: the car's declared abilities.** The diagnostics bundle now lists the ability codes the vehicle itself reports (`VehicleAbility`), with the comfort features called out (seat heating/ventilation, heated steering). This is ground truth for what a given model actually supports — so remote-control gaps (like the T03's fan/off, #67) can be told apart from "the car doesn't have that feature," and a new model (e.g. the B05) is understood the moment it connects rather than assumed. Read-only; all models. The poller stores the abilities on start, so restart the add-on once on this version before downloading a fresh diagnostic.

## [2.1.4] — 2026-07-03

### Fixed
- **T03: A/C on, temperature, fan and recirculation now take effect** (#67, thanks @rossiadobe for the live logs). On the T03 these controls were sent as an `operate=auto` climate command, which the car accepts (the cloud returns success) but silently ignores — so the **A/C** button didn't start it, and the **temperature**, **fan** and **recirculation** changes did nothing. On the **T03 only** they now send the `operate=manual` command the car actually honors (the same path the working **Cool** button already uses); when no manual mode is active, the A/C button starts in cooling. **B10/C10/B05 are completely unchanged.** (The separate **A/C Off** button is still under investigation — the T03 ignores the off command too, which needs the car's real off command.)

## [2.1.3] — 2026-07-03

### Changed
- **Richer support diagnostics.** The diagnostics bundle's cost & wallbox section now shows, per charge, the exact stored `ac_energy` value, whether the card is billing on the wallbox (AC) or the battery (DC), and the wallbox counter baseline — so cost/wallbox questions (#109) can be diagnosed at a glance. Combined with the A/C-off command logging already added in 2.1.2 (#67), a single fresh diagnostic now captures exactly what the car reports and does.

## [2.1.2] — 2026-07-03

### Fixed
- **T03: an explicit "A/C Off" button** (#67, thanks @rossiadobe for the live logs). On the T03 the car reports its climate as "off" even when it's actually running, so the climate tiles could never switch it off. A dedicated **A/C Off** button now appears **on the T03 only** and sends the off command directly, bypassing that unreliable state. B10/C10/B05 are completely unchanged.
- **Charges: removed the "actual charge" time window** (#109, thanks @riri19). It was derived from the last sample with live power, which under-reports the end time whenever the car sleeps mid-charge (the cloud stops reporting power) — showing a confusing, wrong end time. Removed.

### Added
- **Diagnostics: cost & wallbox section** — the shareable diagnostics bundle now includes the pricing mode per charge type, base prices, the wallbox entity mapping and the last few charges (DC / AC / cost), so cost questions can be diagnosed from a single file.

## [2.1.1] — 2026-07-02

### Fixed
- **T03: the A/C now turns off** (#67, thanks @Gr1m214 for the report). On the T03, Mate's "A/C off" was accepted by the cloud but ignored by the car — you could switch the climate on, never off. The off command is now **model-specific**: the T03 uses the car's dedicated `ac_off` action, while the **B10 and C10 keep their confirmed off command unchanged** (B05 too). No change to how you use it — "A/C off" simply works on the T03 now.

## [2.1.0] — 2026-07-02

### Added
- **Automatic preparation when the car turns on (Ready).** A new automation on the **Vehicle Preparation** page fires the exact same one-shot preparation as tapping **Prepare Now** — climate, windows, per-seat heating/ventilation, heated steering and mirrors — but automatically, the moment the car goes **Ready** (powered on). You configure once what it should do; it then runs by itself on every start.
  - **Optional interior-temperature condition.** You can gate it on the cabin temperature: run *only* when it's **above** a threshold (e.g. pre-cool above 25 °C) or *only* **below** one (e.g. pre-heat below 5 °C). **Leave the condition off and it runs on every Ready, unconditionally.** The check is on the **interior** temperature — the cloud exposes no outside-temperature signal — and it's evaluated **once**, at the instant the car turns on (a later temperature change during the same drive won't re-trigger it).
  - **Fires once per Ready session**, not repeatedly: it won't re-run while you stay on, nor for a later trip within the same on-session. It's debounced against brief signal blips and is restart-safe (a poller restart never re-fires a preparation that already ran).
  - Windows snap to the levels the car actually honors (0 / 20 / 50 / 100 % on the B10), the same as the manual control.
- **User notes on charges and trips** (#107, thanks @riri19 for the suggestion).
  - A free-text **note on each charge** (station location, shade/shelter, reliability, parking, weather, personal remarks) — context the raw numbers can't capture, right above *Delete charge*.
  - A free-text **note on each trip**, plus **manual driving tags**: drive mode (Comfort / Normal / Sport) and One-Pedal (on/off). The Leapmotor cloud doesn't report drive mode or One-Pedal, so these are set by hand — they help explain why two similar trips consumed differently.

## [2.0.2] — 2026-07-02

### Fixed
- **Dynamic pricing no longer mis-prices charges away from home** (#106, thanks @twiktorowicz for the report). Since 2.0.0, selecting the Dynamic mode applied your home tariff sensor to **every** charge — including public AC/DC/HPC sessions, which the operator bills at their own fixed price, not at your home spot price (on spot markets some hours cost close to zero, so away-charge costs could be wildly wrong or near-free, silently). The pricing mode is now **per charge type**: Home, AC, DC and HPC each pick Fixed (24h) or Time slots, and Home can also go Dynamic (no HA integration exposes a price for public charging, so Dynamic is offered on Home only). Existing dynamic setups are corrected automatically on update: your sensor keeps pricing **home** charging exactly as before, while public types return to their fixed base prices. Fixed and Time-slots setups are untouched. Already-computed costs stay frozen as always — the fix applies to future charges (re-confirming a charge's type badge recomputes it).
- Selecting Dynamic no longer locks you out of the other modes: time bands and fixed prices now coexist with it per charge type (e.g. home on the tariff sensor **and** a public AC network on its own time bands). A time-band price cell for a type not currently on Time slots is greyed out and disabled — it used to look live and editable while silently never being used.
- While Home is Dynamic, its base-price field is hidden rather than shown as editable (it's a fallback only, used when the sensor misses or a charge is still in progress) — an editable price sitting right next to "Dynamic" looked like the real, current price.

### Added
- **Groundwork for total-energy statistics**: once a day Mate now quietly records the car's official lifetime energy and mileage counters from the cloud (the same "total energy" the vehicle itself reports, parked/standby consumption included) alongside the official driving split for the same window. Nothing visible yet — this ledger is what upcoming features (total & parked energy per period) will be built on, and the earlier you run a version that records it, the further back those stats will reach.

## [2.0.1] — 2026-07-02

### Fixed
- **No more impossible average consumption on driving-energy windows that start before Mate was installed** (#105, thanks @riri19 for the report). The official cloud figure covers the car's whole life, but Mate's local trip history only starts at install — pairing the two on the "Since the beginning" preset (or any custom range starting earlier than your first recorded trip) produced a meaningless kWh/100km. Such windows now show the official energy split alone, with a note giving the date your local trip stats begin; ranges fully covered by Mate keep the Distance/Duration/Average row as before.

## [2.0.0] — 2026-07-01

### Added
- **Dynamic electricity pricing from a Home Assistant sensor** (#104, thanks @Wartopia for the idea). If you're on a dynamic/spot-price contract (Nordpool, Tibber, ENTSO-E, or your utility's own integration), Mate can now price each charging session from that sensor's history instead of a fixed price — new "Dynamic (HA sensor)" mode on the Prices page, next to Flat and Time-of-use. **Requires Home Assistant** (add-on or a standalone install connected to HA) with a price-per-kWh sensor to point at; without one, or whenever it's briefly unreachable, Mate falls back to your flat base price so a session is never left uncosted.
- Not a breaking change — if you don't use it, nothing changes. Bumped to 2.0.0 as a milestone after a month of near-daily releases, not because anything existing behaves differently.

## [1.36.2] — 2026-07-01

### Added
- **Distance, duration and average consumption alongside every official driving-energy card** (Statistics' day/week/month/all-time/since-last-charge, and the Monthly Report) — the same figures the car's own "since last charge" screen shows next to its Driving/A/C/Other split.

### Fixed
- **The Monthly Report's "Average consumption" and "Energy used" tiles now match the official cloud figure shown just above them.** They previously came from summing each trip's own stored value (which falls back to the SoC estimate for the rare trip without cloud data yet), so on a short or recent period they could show a visibly different number than the live official figure right next to them. Both now read from the same official source. Navigating to a past month with ◀/▶ shows that month's own official total (the day/week "quick" tabs only make sense for the current month, so they're hidden on a past one).

## [1.36.1] — 2026-07-01

### Added
- **"Since last charge" driving energy card (Statistics page).** Shows the official cloud consumption split (Driving / A/C / Other) for everything driven since the end of your last completed charge — same live figure as the day/week/month/all-time cards, just anchored to your last charge instead of a calendar period.

## [1.36.0] — 2026-06-30

### Added
- **Map cursor synced to the SoC/speed chart on the trip detail page.** Hovering the chart now shows a cyan marker on the map at the exact GPS position recorded at that moment, following the cursor as it moves and disappearing when the mouse leaves the chart — makes it easy to spot where on the route a speed change or SoC drop happened. (#102, thanks @hubcasale)

## [1.35.1] — 2026-06-30

### Fixed
- **Official trip consumption (`getEC`) no longer occasionally comes back empty and drops a trip to the estimate.** The query now starts from the **last moment the car was confirmed OFF** (the sample just before power-on) instead of the first poll that detected "Ready". Polling is periodic (~30 s while the car is off), so that first "Ready" poll can land a few seconds *after* the cloud's session anchor — making the official figure return nothing and the trip fall back to the SoC estimate. Anchoring to the last "off" sample is guaranteed to be before the anchor at any polling cadence, so the official figure is caught reliably. (The same trip could succeed on one device and miss on another purely from polling phase — a 4-second difference was enough.)

## [1.35.0] — 2026-06-29

This release makes Mate use the car's real **power-on (Ready) signal** to bound both the official trip consumption and the battery-drain calculation — replacing time-based guesses with the exact moments the car was switched on and off.

### Changed
- **Official trip consumption is now measured from when the car is switched ON (Ready), not from when driving starts.** The cloud's official figure (`getEC`) covers a whole power-on session — from the moment you power up until you switch off — so Mate now aligns its query to that exact window instead of guessing "2 minutes before the drive". This is more accurate and removes the occasional missing/inflated values on the first try.
  - **If the car is never switched off between two trips** (e.g. you stop, stay in Park with the engine on, then drive again), the cloud counts them as **one session**. Mate detects this and asks you to **merge the trips** to get the real combined consumption — merging is now allowed at any gap for trips that share one power-on session, and converts the combined drive over its full distance.
  - A trip whose official figure is implausible versus the battery's actual SoC change is still kept on the reliable estimate (no impossible numbers), and every conversion stays reversible with **"Revert to estimate"**.
- **Battery (vampire) drain is now measured from when the car is COMPLETELY OFF — power-off to the next power-on.** Everything the car consumes while off is counted as drain, **including remote heating/cooling (pre-conditioning) done while the car is off** — by design: car off → it's drain. On‑state idle (parked with the engine on / climate running) is no longer mixed into the drain figure (it belongs to the trip). The Battery-health card explains this.

### Notes
- Both calculations fall back to the previous behaviour for trips/periods recorded before the Ready signal was logged, so existing history is unaffected.

## [1.34.2] — 2026-06-29

### Fixed
- **"Convert with official data" no longer applies an over-inflated cloud figure on very short trips.** On a ~1 km trip the cloud's official consumption (`getEC`) can come back several times larger than the energy that actually left the battery — its window includes the ~2 minutes before you start driving (A/C, standby), which dwarfs a tiny drive — giving an impossible figure like **120 kWh/100 km**. The plausibility guard is now **symmetric**: it refuses a value that is both absurdly **high** *and* far above the trip's real battery use, exactly as it already did for absurdly **low** / incomplete values (#96), keeping the reliable estimate. The message now notes that on very short trips the cloud isn't precise. (Reported in #98.)

## [1.34.1] — 2026-06-29

### Fixed
- **"Convert with official data" no longer overwrites a good estimate with an incomplete cloud figure.** On some trips the cloud's official consumption (`getEC`) comes back only partially aggregated — a value that would imply an impossible efficiency (e.g. a 33 km drive showing **1.5 kWh/100 km**). The button now **refuses** such a value and keeps the reliable SoC estimate, cross‑checking the cloud figure against the trip's actual battery use (ΔSoC × pack) so a genuinely low‑consumption trip is still accepted. No data is ever lost. (Reported in #96.)

### Added
- **"Revert to estimate" button** on a converted trip — undo an official‑data conversion and restore the SoC estimate at any time (you can convert again later). Every conversion is now fully reversible from the trip page.
- **Arrival odometer** on the trip detail, right under the start odometer. (#95)
- **Regeneration and cost per period** — the kWh recovered and the period's cost are now shown on the **year / month / day** rows of the Trips list, next to the existing distance and consumption. (#95)

### Changed
- **"Efficiency" is now labelled "Average consumption"** across the app (Trips, Statistics, Report, trip detail) — closer to the universal automotive wording. Charge efficiency (%) is unchanged. (#95)

## [1.34.0] — 2026-06-29

### Added
- **Official trip consumption from the Leapmotor cloud.** Each trip's **energy, efficiency and cost** now use Leapmotor's **official figure** — the real **driving / A·C / other** split from the cloud (`getEC`) — when it's available, instead of only the battery‑% (SoC) estimate. The trip detail shows the **breakdown**, and the official numbers flow through into the **Report** and **Statistics** aggregates. While the cloud is still aggregating a fresh trip the figure is the SoC estimate marked **⏳ provisional**, then it **swaps to the official number on its own** once it stabilises (the SoC value is kept as a fully reversible backup). The feature is **always on** and degrades gracefully: if the cloud hasn't recorded a trip — which happens occasionally on any connected car — that trip simply stays on the SoC estimate, never an error.
- **"Convert with official data" button** on any trip that isn't official yet — it works for older trips too (the cloud retains the data well beyond a week). If two trips were really one drive split by a very short stop, it tells you to **merge** them; merging then converts the **combined** drive automatically over its full distance (Unmerge reverts cleanly).
- **"Vehicle Cumulative Total" card** (Statistics) — since‑delivery **total energy, total mileage and lifetime kWh/100 km** read from the car's own lifetime counter, plus a **driving / A·C / other / standby** split. The total is counter‑based (not a sum of per‑trip figures), so it is **not affected** by the occasional missing‑trip gaps.
- **Official driving‑consumption views** — Today / This week / This month cards (Report) and a custom date‑range query (Statistics), with the driving / A·C / other split from the cloud.

### Changed
- **Trips list — "Collapse days"** now collapses only the day groups (the month/year structure stays), and collapsing a month re‑collapses its days so re‑opening it is tidy. **Merge mode** no longer expands the whole archive — it opens only the days that actually have a joinable pair. The merge **default gap is now 5 minutes** (a longer stop is a real, separate trip); the slider still opens up to 90 minutes for manual merges.

### Notes
- **Upgrade‑safe**: the new per‑trip columns are added by an automatic, additive migration (verified on a real pre‑feature database — no rows lost, existing trips unchanged). Historical trips keep their SoC values; only trips from the update forward are enriched automatically (older ones can be converted by hand).

## [1.33.2] — 2026-06-27

### Fixed
- **Sidebar no longer gets stuck visible at narrow widths after opening the mobile menu.** The off-canvas hide below the `md` breakpoint relied on a Tailwind utility class that the menu toggle itself stripped, and nothing reset the drawer when the viewport crossed the breakpoint — so opening the menu and then resizing could leave the sidebar wrongly showing at narrow widths (it would "reappear" at some resolutions). Open/close now uses a dedicated state class (the hide stays owned purely by CSS) and resets when entering desktop width.
- **Overview hero card: the "doors locked" and "windows closed" status chips no longer overlap.** On a narrow hero card — the 3-column desktop layout, or an iPad viewing the add-on inside Home Assistant's ingress iframe — the two corner-pinned chips printed on top of each other. They now share one self-adapting row that places them at opposite corners when there's room and stacks them when the card is narrow, independent of the label's translation length.

## [1.33.1] — 2026-06-25

### Fixed
- **Windows slider no longer shows a stale opening % when the windows are closed.** The B10 can't report its exact window position, so the slider shows the last commanded %; if the windows were then closed via the official app, physically, or the toggle, it could sit at e.g. 100% while the state badge correctly read "Closed". The slider (and its % label) now snap to **0%** whenever the windows are reported closed, matching the badge; open/unknown states are unchanged. The other sliders were audited and are not affected (climate temperature/fan read live signals; seat heating/ventilation track the polled state; settings sliders are configuration values).

## [1.33.0] — 2026-06-24

### Added
- **REEV (range-extender) support — first step.** Setup now offers the **REEV battery variants for the B10 and C10** (the T03 has no REEV), and a new dedicated **REEV** page surfaces the fuel sensors — **fuel level, fuel range and combined battery+fuel range** — for range-extender cars. The page appears automatically on a REEV (detected from the car's fuel signal) and stays hidden on full-electric cars. It is **read-only for now**: REEV data is not yet woven into trips, charges or statistics — that follows once the behaviour is validated on real vehicles. Mapping based on the community work in kerniger/leapmotor-ha#46.
- **Separate MateBetaTesterOnly build (opt-in).** A distinct beta image/repository, kept in lockstep with this one from the same source, captures the full raw-signal history + a tester logbook and exports an **encrypted** bundle — so we can decode REEV behaviour and ship full REEV support in a future release. The normal build is completely unaffected (the research code is inert unless explicitly enabled).

## [1.32.1] — 2026-06-24

### Fixed
- **MQTT "Test connection" no longer fails with "Not authorised" when the password field is left blank (#91).** The MQTT password field is masked — it shows `••••••••` but never carries the real value — so clicking *Test connection* without re-typing the password tested with an **empty** password and the broker rejected it as "Not authorised", even though the background MQTT bridge and the top-right status dot (both using the **saved** password) stayed green. *Test connection* now falls back to the saved password when the field is left empty, exactly like Save and the status indicator already do; if you type a new password it still tests that one, so verifying new credentials before saving keeps working.

## [1.32.0] — 2026-06-24

### Added
- **Saved wallbox profiles.** If you charge in more than one place — e.g. two homes, each with its own wallbox — you can now save a wallbox configuration (the Home Assistant connection, the entity mapping **and** the electricity tariff) under a name and switch between them in one click from **Settings → Wallbox**, instead of re-entering everything each time. The active profile is shown on the **Costs** page; switching is **blocked while a charge is in progress** so a session is never split across two wallboxes, and the per-profile **tariff travels with the profile** so each home's costs stay correct. Community feature contributed by **@domevite** (#84).

## [1.31.0] — 2026-06-24

### Added
- **Polish language (Polski). 🇵🇱** The whole UI is now available in Polish — the app, the settings **and** the first-run setup wizard — selectable in **Settings → Language**, or auto-detected from the browser on first launch. Dates use Polish month names, and the **złoty (zł)** is available as the display currency. Community translation contributed by **@irek** (#90), completed with the latest v1.30.0 strings, the setup wizard, Polish month names and `lang_pl` in every language, and normalised to the repo's line endings.

## [1.30.0] — 2026-06-24

### Added
- **Add a past charge by hand.** A new **➕ Add a past charge** form on the Charges page lets you backfill charging sessions from before you started using Mate, so the lifetime totals and the monthly report are complete. You enter just the date, energy (kWh), optional cost and AC/DC — manual entries carry no SoC or power curve, so they're excluded from the Battery-Health estimate and never have their cost overwritten by the automatic coster. (Requested by twiktorowicz, #87.)

### Changed
- **Statistics trend charts are now per **day**, not per trip.** The distance / efficiency / regen mini-charts under each month aggregate by day, so a handful of short hops no longer crowds the axis or spikes the efficiency line — the trend is far easier to read. (Suggested by riri19, #86.)
- **Vampire-drain chart gets two view toggles + a duration filter.** You can switch the bars between **%/day** (the normalised rate) and **% lost** (the actual SoC dropped during the stop), and between **per stop** and **per day** (one aggregated bar per calendar day) — so short parks no longer show alarming extrapolated spikes. A new Advanced setting, **minimum parking time**, hides parked stretches shorter than the chosen number of hours. (Suggested by riri19, #88.)
- **"Best efficiency" now ignores tiny trips.** The headline best-efficiency figure only considers trips of at least 15 km, so a short downhill coast or a glitchy frame can't masquerade as your record. (Suggested by riri19, #86.)

## [1.29.3] — 2026-06-24

### Fixed
- **Command feedback on the Prepare Car page.** Sending an immediate preparation or saving/deleting a schedule gave no on-screen feedback, so it wasn't clear whether the command had been sent, was waiting for the car, or hadn't fired at all (the cloud round-trip can take many seconds). Each of these now shows a **⏳ "Sending…" / "Saving…"** indicator the moment you submit, which stays until the car responds and is then replaced by the ✓/✗ result — matching the rest of the app.
- **"Charging" status label no longer looks stuck on.** On the Charges page the live panel's heading always read "Charging" (a section title in English), which in other languages reads as the live state — so it looked like the car was always charging even when the panel's own badge correctly said "Not charging". The heading is now a neutral **"Charging status"**; the real state is shown only by the badge. (Reported by riri19, #85.)

### Added
- **Delete account / Factory reset.** Settings now has a destructive **🗑️ Delete account / Reset** action (next to Log out) that wipes *everything* — the account, all trips, charges, positions and every setting (MQTT / wallbox / prices / Home Assistant) — and reopens the setup wizard as a brand-new install. Unlike **Log out** (which keeps your history, keyed to the car), this keeps nothing except the app-level certificate on disk, so re-onboarding still needs only your e-mail, password and PIN. It is **type-to-confirm** (you must type `RESET`) because it cannot be undone.

### Fixed
- **Onboarding no longer gets stuck after linking a freshly-shared car.** Right after a shared car is accepted, the Leapmotor cloud can briefly reject requests with a transient "verification failed, try again later" error until the share propagates. Two things handled that badly: the poller's **first login** let the error crash the process, which then restarted in a tight loop that hammered the cloud; and the **car picture** could be saved as the error response itself, leaving the Overview with no image until a manual refresh. The first login now **retries in-process with a backoff** (and picks up a corrected login at once) instead of restart-looping, and the car-picture download is now **validated as a real image** before it is cached. Both recover on their own.

### Changed
- **Comfort card laid out in two columns** on the Commands page (was three) — larger, easier-to-read seat / steering / mirror tiles.

## [1.29.1] — 2026-06-22

### Fixed
- **Window remote-control on the C10 and B05.** cmd 230 now maps the uniform 0–100% slider onto their native 0–10 range and snaps to the steps the car actually actuates (0/20/50/100%), matching the B10. The C10 scale was confirmed on-car (thanks @kerniger / leapmotor-ha, discussion #47); the B05 shares the B10 platform and battery pack. Previously both models fell back to the 0–100 default, so any slider or vent value above ~10% was silently ignored by the car.

## [1.29.0] — 2026-06-22

### Added
- **Mobile-friendly Trips / Charges / Statistics, and quicker navigation.** On phones (< 640 px) the summary "hero" tiles now **stack vertically** so labels like *Total distance* / *Total trips* are no longer truncated — the tablet/desktop layout is unchanged. Each of the three pages gains a **"Collapse all"** button that folds the whole year/month tree in one click, and every open year/month row gets an inline **⊟** to collapse just that section; the open/closed state is remembered across reloads. A floating **back-to-top** button (↑) now appears on every page once you scroll down. (Thanks @hubcasale — #83.)

## [1.28.1] — 2026-06-22

### Fixed
- **Charges now state which energy figure they show.** When Mate has no wallbox reading for a home charge it shows the energy that reached the battery — but that number used to appear with no label, so it could be mistaken for the higher "billed" (grid) amount. Each charge's energy now says what it is: **🔋 In battery (DC)** (what actually entered the battery) or **🔌 wallbox (billed)** (what the wallbox drew from the grid — what you pay). A tooltip on the battery figure explains it is ~10–15% lower than the grid draw, because of AC→DC conversion losses. (Reported by riri19, #80.)

## [1.28.0] — 2026-06-21

### Added
- **Climate panel — fan, recirculation and per-mode manual control.** Mate now reads three more things from the car and lets you set them. It reads the **fan level** (1–7), **air recirculation** (fresh air / recirculate) and the **active climate mode** (AUTO · Cool · Heat · Vent), shows them on the Vehicle card, and publishes them to Home Assistant over MQTT Discovery — a **writable `Fan Level` number**, a **writable `Recirculation` switch**, and a **`Climate Mode` sensor**. On the Commands page the climate card gains a **temperature slider, a fan slider and a recirculation toggle**: in the three manual modes (**Cool · Heat · Vent**) you can set the target temperature and the fan speed and the car **stays in that mode and remembers the value**; in **AUTO** the car manages fan and recirculation itself, so those two controls show the current value but stay read-only (the temperature remains adjustable as the AUTO target). Each climate tile (A/C AUTO · Cool · Heat · Vent · Defrost) now lights from the car's **real mode**, so exactly one is lit at a time and it matches the official app. Discovered and validated entirely by on-car testing on a B10 — including correcting the Leapmotor library, which mislabels the fan-level signal.

### Fixed
- **Rapid Ventilation now actually engages.** Pressing **Ventilazione Rapida** used to leave the car on whatever mode was already active unless you started from a neutral state; it now reliably switches the car into **true ventilation** (air only — no heating, no cooling) from **any** state, exactly like the official app.
- **No more slider "snap-back".** Moving the temperature or fan slider could briefly jump back to the old value before the car caught up — the value you set now stays put while the car re-polls within a few seconds.

## [1.27.0] — 2026-06-19

### Added
- **V2L (vehicle-to-load) monitoring — a first for any Leapmotor tool.** When the car powers an external device through the V2L adapter, Mate now shows it. The Overview gets a live **V2L block** (status, instantaneous **net power** in watts with a 0–3500 W bar, and energy drawn this session) that refreshes every **10 s** while a session is running; the Statistics page gets a **Total V2L** card with the cumulative energy drawn over all time; and **three MQTT entities** are published to Home Assistant — **`V2L Active`** (binary), **`V2L Power`** (W) and **`V2L Session Energy`** (Wh). Power is reported **net of the car's own ~300 W overhead** (the idle baseline frozen when V2L starts), so it matches what your device actually draws — not the gross battery output. It's **read-only** (V2L can't be triggered remotely: the car needs Park + a connected device). Discovered entirely by on-car testing — the car reports V2L on the battery current/voltage signals plus an AC-port flag, with an honest resolution floor of **~42 W** (the car's own 0.1 A current resolution, so a 10 W phone charger stays invisible). While V2L is active the poller drops to a **10 s** cadence so power changes aren't missed, and V2L discharge is **excluded from the parked "vampire drain"** metric so a session powering a fridge never reads as battery loss.

### Fixed
- **The registration / delivery date on the Maintenance page can now be corrected.** Once set, it used to become read-only text with no way to fix a typo; it's now an editable field — click **✏️** next to the date to change it (the new value overwrites the old one).

## [1.26.0] — 2026-06-19

### Added
- **The charge limit (target SoC) is now a writable Home Assistant entity.** A **`Charge Limit` `number`** (50–100 %) is published over MQTT Discovery: it shows the limit Mate already reads from the car and lets you set it straight from Home Assistant dashboards and automations — not only from Mate's Prepare-Car page. It reuses the same `set_charge_limit` command the web UI has always used, so there's no new car behaviour to trust. (Community request, #77.)

## [1.25.2] — 2026-06-19

### Fixed
- **Running the standalone Docker image no longer dead-ends — the UI is reachable, data persists, and "Try the demo" works without a restart policy.** Three rough edges that hit anyone running the published image directly (especially via Docker Desktop's **Run** button):
  - The image now declares **`EXPOSE 4000`**, so Docker Desktop's Run dialog pre-fills the port mapping instead of showing *"No ports exposed in this image"* (which left the UI unreachable).
  - It now declares **`VOLUME /data`**, so trips/charges/login persist in an anonymous volume even if you forget `-v ...:/data`, instead of vanishing with the container.
  - The data directory is now **created explicitly** at startup (it used to be created only as a side effect), with a clear error if `/data` isn't writable — no more cryptic *"unable to open database file"* crash on first boot.
- **In-app relaunches ("Try the demo" / "Exit demo" and the account switch) no longer need a container restart policy.** They used to rely on the orchestrator recreating the container; a plain `docker run` / Docker Desktop "Run" sets no restart policy, so pressing **"Try the demo"** stopped the container and looked like a crash. The entrypoint now relaunches Mate **in-process**, so the toggles work everywhere. (Home Assistant add-on and policy-managed setups are unaffected.)

### Changed
- Removed an unused leftover translation key (`cmd_wait_next`) in all four languages.

## [1.25.1] — 2026-06-18

### Changed
- **Command feedback now shows only the car's REAL state — clearer and consistent across the Overview and the Commands page.** Pressing a control no longer optimistically guesses the outcome: the tile you pressed shows a **"⏳ command in progress"** message until the car actually confirms the new state, every control is briefly disabled while a command completes so you can't fire a second one before the first is accepted (no more "not sent — retry in Ns" surprises), and the Commands grid now refreshes **live** so what you see always tracks the car. The same mechanism covers the Overview hero quick-commands and the whole Commands grid — vehicle (lock / trunk / windows / sunshade), climate, comfort toggles (steering & mirror heat) and the seat-level sliders — in EN/IT/FR/DE.

### Fixed
- **B10: a non-binary window status flag (e.g. `2`) is read as OPEN.** Confirmed live against the official app on a B10 — `2` is a genuinely open window. This reverts the 1.24.1 reading (which treated `2` as closed, from what turned out to be a stale cloud frame); transient/stale frames are now handled by the real-state polling above rather than by re-interpreting the flag.
- **Web log lines — and the Diagnostics bundle — are no longer doubled.** `uvicorn.run("main:app")` re-imports the app module a second time in the same process, so the rotating-file-log setup ran twice and attached two handlers to the same file: every web line was written twice, doubling both the log and the diagnostics export. The setup is now idempotent (the handler is added once); the poller was never affected. (Spotted in @riri19's diagnostics export.)

## [1.25.0] — 2026-06-18

### Added
- **Home Assistant: a single "Trunk" toggle ([#71](https://github.com/ProtossBlaster/leapmotor-mate/issues/71), thanks @wlighter).** A new `switch` entity that shows the trunk's open/closed state **and** opens or closes it from one control — the trunk analog of the existing "Door Lock Toggle" (ON = open, OFF = closed). It reuses the `trunk_open` state and routes to the existing open/close commands; the separate Open/Close Trunk buttons stay for anyone already using them in automations.

### Fixed
- **Remote commands are no longer fired twice (and the session is no longer needlessly re-logged in) when the car doesn't confirm in time.** When a command timed out waiting for the car to acknowledge — the cloud accepted it (HTTP 200) but the car, parked and asleep, never confirmed — Mate misread the "timed out" message as a *network* error, reset the session (forcing a re-login) and **sent the command at the car a second time**. A car-confirm timeout is now recognised as exactly that: logged best-effort, with no reset, no re-login and no resend. Genuine connection errors still reset and retry as before. (Surfaced from @riri19's logs, [#73](https://github.com/ProtossBlaster/leapmotor-mate/issues/73).)
- **Battery health: charges distorted by a BMS SoC recalibration or by active cabin use are now excluded from the capacity/SoH figure ([#72](https://github.com/ProtossBlaster/leapmotor-mate/pull/72), thanks @hubcasale).** Two cases produced artificially low capacity estimates: (1) the BMS occasionally snaps SoC upward without matching energy, inflating ΔSoC (detected on AC charges as a SoC rise > 0.8 %/min, ≈ 2× the physical AC ceiling); (2) the cabin A/C or heater running while plugged in feeds part of the charger energy to car loads instead of the battery (detected via climate cooling/heating during the session). Both are now excluded from the headline figure but still shown on the chart in amber for context, with per-reason labels and an explanatory note. The vampire-drain headline likewise excludes windows where the car was in active use (drain rate > 15 %/day). Four new strings in EN/IT/FR/DE.

## [1.24.2] — 2026-06-18

### Fixed
- **Home Assistant: "A/C Off" over MQTT now actually turns the climate off after a Quick Cool/Heat ([#67](https://github.com/ProtossBlaster/leapmotor-mate/issues/67), thanks @Gr1m214).** The MQTT "A/C Off" button is guarded so that pressing it when the climate is already off is a harmless no-op. That guard read a reference (`last_climate_on`) that was only ever updated by a *poll* — the optimistic state published right after a command didn't update it. So immediately after a Quick Cool/Heat (before the next poll caught up) the reference still said "off", and the following "A/C Off" was **silently skipped**. The guard now stays in sync with the optimistic state, so "A/C Off" fires as expected. The web "A/C Off" button was never affected (it sends the command directly, without that guard). Verified end-to-end on a B10: Quick Cool → A/C on → A/C Off → A/C off. _Note: `ac_switch operate=off` is confirmed to fully switch the A/C off on the B10; on other models the cloud may accept but ignore it — a separate, model-level limit._

## [1.24.1] — 2026-06-17

### Fixed
- **B10: windows no longer shown open when they are shut ([#68](https://github.com/ProtossBlaster/leapmotor-mate/issues/68), thanks @riri19).** Some B10 firmware reports the window status flag with a non-binary value (e.g. `2`) on a *fully closed* window, while the actual opening position reads 0%. Mate treated any non-zero flag as "open", so the Overview tile, the Commands grid and the new live car image all showed a window — and the "windows open" count — open when it wasn't, and a *close windows* command couldn't self-confirm (it timed out and reverted). Mate now trusts the opening position when the car reports it (0% = shut) and treats only the canonical flag value as open, so a closed window reads closed everywhere. The T03 (position-driven) and the genuinely-open cases are unchanged.

## [1.24.0] — 2026-06-17

### Added
- **The Overview car picture is now live.** Instead of a single static render, Mate composes the car image from the per-vehicle layer package it already downloads — so it reflects the real state: the **charge cable** (animated while charging), the **four doors**, the **two near-side windows**, and the **tailgate**. It updates the moment the state changes (and right after a command), and falls back to the static render if anything is unavailable. The interactive **demo** shows it too (charging animation out of the box). The model and colour come from the car's own package, so it works for any model.

### Changed
- **Unified colour system across Overview, Commands and Vehicle ([#66](https://github.com/ProtossBlaster/leapmotor-mate/issues/66), thanks @riri19).** One consistent meaning for every colour so the eye can read state at a glance: **green = safe** (locked, closed), **amber = attention** (windows open), **red = alert** (trunk open, unlocked); **blue = cold** and **orange = hot** for climate and comfort; **teal** for non-critical/brand (sunroof shade, charts); and a **neutral grey** for any control at rest (no more "rainbow" of per-button colours). The Overview gained a **"trunk open" chip**, and the battery standby chart now uses the brand colour. State is shown by which control is highlighted plus the status chips — the action buttons stay neutral.

## [1.23.1] — 2026-06-17

### Fixed
- **T03: the Overview tile and the Commands page now show the windows open (with the count) — not just the Vehicle page.** The window open/closed state was computed in several places, but only the Vehicle page had the 1.22.6 T03 fix. The Overview "windows" chip (and its open-count badge), the Commands grid and the post-command verification still read only the open/closed flags — which the T03 leaves at 0 even when the windows are open — so they kept showing "closed", and "open windows" never self-confirmed (it timed out after ~30s and reverted the optimistic state). All four surfaces now share a single position-aware reader, so they agree and the **"windows open" count** is correct on the T03 too. The B10 is unaffected: it reports no window position at all, so it stays flag-driven exactly as before. (#62)
- **Overview hero card: quick-command feedback is now immediate.** Pressing a quick command (lock/unlock, trunk, windows) on the car card only updated the visible state on the next 30-second auto-refresh, so it looked like nothing had happened and you couldn't tell whether the command had worked. The card now refreshes right after a command — an instant flip from the optimistic state, then it reconciles with the car as the cloud catches up (the same behaviour the Commands page already had). The window-open count flips with it.

## [1.23.0] — 2026-06-17

### Added
- **Window position control + per-window opening %** ([#62](https://github.com/ProtossBlaster/leapmotor-mate/issues/62)). The Commands page now has a position slider for the windows, next to the quick "vent" button. It snaps to the stops each model actually actuates — the **B10** opens to 4 discrete positions (closed / ~20% vent / ~50% / fully open; it uses a 0–10 scale and ignores everything else, confirmed on-car), while the **T03** is continuous 0–100%. Opening asks for confirmation like the buttons, the slider reflects the last commanded position, and it triggers a fast status refresh so the change shows immediately.
- **The Vehicle page now shows each window's opening %.** The real value on cars that report it (T03), and the last commanded position on cars whose window-position sensor is dead (B10) — shown only for windows the open/closed flag confirms open, so a closed window never shows a stale number.

## [1.22.6] — 2026-06-16

### Fixed
- **T03: the window open/closed status now reflects reality, and "open windows" works.** On the T03 the Vehicle page always showed the windows as "closed" — it read only the open/closed flags, which the T03 doesn't drive (it reports the live window *position* instead) — and the "open windows" button did nothing, because the command value the T03 needs differs from the B10's. Mate now also reads the window position where it's a reliable signal for the car (gated per-car via the capability profile, so the B10's dead position-sensor can't produce false "open" readings), and sends the model-appropriate open value. The B10 is unaffected (same command, same behaviour as before). (#62)

## [1.22.5] — 2026-06-16

### Fixed
- **No more spurious "Could not find the TLS certificate file" errors and unnecessary re-logins.** The per-login account TLS certificate can be cleaned up mid-session — most visibly on a car with weak mobile coverage that fails many polls — which used to surface as an alarming error and force a full re-login. Mate now re-creates the certificate in place from the copy it already keeps (before each status poll and each remote command), so the session keeps working without a re-login (#64). Note: commands to a parked car on a weak signal or in deep sleep can still time out — that's the car not answering, not a Mate error, and the **Car responsiveness** indicator on the Overview reflects it.

## [1.22.4] — 2026-06-16

### Fixed
- **The battery-standby (vampire-drain) chart no longer looks empty when your display threshold hides every window.** The Advanced "minimum drop" setting is a *display* threshold — raising it above your car's actual standby losses used to blank the whole section with a "no measurable drain" message, making it look as if the history had been lost (#63). Now the typical-rate headline always shows while measurable parked windows exist; the chart adds a "+N below your threshold" note when it hides smaller windows; and when every window is below the threshold the page says so explicitly and points you to lower the slider in **Settings → Advanced**, instead of looking empty.

### Changed
- **Diagnostics bundle now reports the vampire-drain display threshold and reproduces the page.** The computed section uses the same `vampire_min_drop_pct` the battery page does (so a high threshold shows `count=0` here too, with the measurable-window count revealing the real cause), and the header prints the threshold value.

## [1.22.3] — 2026-06-16

### Changed
- **Diagnostics bundle now shows the computed battery-standby (vampire-drain) result and a 14-day SoC-by-day summary.** When someone reports an empty or missing battery-drain chart, the downloaded bundle now includes exactly what Mate computes — the window count, the time-weighted rate and each detected window — plus, for the last 14 days, the daily battery-% high→low and the km driven. That makes the actual cause (sparse data, no qualifying parked period, gaps, etc.) visible at a glance instead of guessing from a screenshot.

## [1.22.2] — 2026-06-16

### Fixed
- **Vampire-drain chart now captures standby loss that only becomes visible when the car wakes.** While a car is in deep sleep it stops reporting and the cloud serves a *frozen* battery %, so a slow standby loss stays invisible — and if you drove off right after waking, that drop fell in the gap between the last parked reading and the trip's start, and was never counted as standby drain (so the chart could look empty). Mate now closes the parked window at the fresh battery % from the wake-into-drive reading, attributing the loss correctly. Charging wake-ups are left out (the pre-charge gap is ambiguous), and the existing reliability flag still marks short/high-rate windows as estimates.

## [1.22.1] — 2026-06-16

### Fixed
- **The Overview map no longer disappears when the car reports no GPS fix.** Parked or in standby, the car can answer a poll with no position — coordinates come through as `0, 0` — and the Overview's "Last position" map was then hidden until the next valid fix. Mate now falls back to the **last known valid position**, so the map keeps showing where the car was last seen (this also gives the Navigation page a sensible starting point instead of a default).

## [1.22.0] — 2026-06-16

### Added
- **Maintenance page.** A new section that tracks your car's factory service schedule, taken from the official owner's manual for your exact model (T03, B05, B10, C10) — never another model's, so each car shows only its own programme. Mate computes what's **overdue / due soon / up to date** from the live odometer and the time since delivery, with a distance bar and a time bar (whichever comes first wins). For a brand‑new car with no history it shows the **first‑service due** from your delivery date; you log each service (date + km) as it's done and the schedule rolls forward. Distances follow your unit setting (km or miles) and the whole page is translated (EN/IT/FR/DE). *(B05 inherits the validated B10 schedule provisionally until its own manual is published; range‑extender/REEV models are out of scope — Mate is BEV‑only.)*

### Changed
- **Diagnostics bundle is richer and easier to share.** "Download diagnostics" now also bundles the car's **raw signals** — with your GPS coordinates stripped — so reporting a model‑specific issue no longer needs any copy‑paste: one click attaches everything, and it stays safe to share publicly. The bundle header also shows the **position‑data span and retention setting**, which makes "my history looks empty" reports (e.g. an empty battery‑health or vampire‑drain chart) diagnosable at a glance. The separate on‑screen "raw signals" view — which showed your live GPS — was removed in favour of this redacted download.

## [1.21.7] — 2026-06-16

### Fixed
- **Front-seat heat/vent tiles are labelled "Driver / Passenger" again, not "Left / Right".** The per-seat comfort *command* is role-based — Mate sends `driver`/`co-driver` and the car maps the role to the physical side for its market — so the fixed "Left/Right" labels introduced in v1.14.0 were inverted on right-hand-drive (UK) cars: turning on "Right seat" actuated the seat the official app shows on the **left** ([#61](https://github.com/ProtossBlaster/leapmotor-mate/issues/61)). Restored the role labels across the Commands tiles, the one-touch Prepare-car screen and the MQTT entities (object_ids unchanged → no Home Assistant churn), so Mate now matches the official app on both LHD and RHD. **Doors, windows, tyres and mirrors are unaffected** — those are physical-position signals (e.g. doors come from the left/right body-control modules) and correctly stay Left/Right.

## [1.21.6] — 2026-06-15

### Added
- **"Car responsiveness" indicator on the Overview.** A small coloured dot next to the status card shows how reliably your car has been answering remote commands lately — a proxy for the mobile coverage where it's parked (🟢 good · 🟡 patchy · 🔴 poor · ⚪ no data yet). It's built from the outcome of your **last 24 commands** — only "car confirmed in time" vs "car didn't confirm" count, since a cloud/network or PIN error isn't the car's fault — and recovers to green within a handful of good commands. A poll only reads the cloud's *cached* state, so a command is the one moment Mate reaches the car in real time, which is exactly what this measures. Hover for details.

### Changed
- **Clearer message when a command "times out".** When you send a command and the Leapmotor cloud accepts it ("request successful") but the car doesn't confirm in time — typically weak cellular coverage or the car in deep standby — Mate now shows an **amber "sent — the car didn't confirm in time (it may still have worked); try again shortly"** notice instead of a red error. The command often *did* apply: it's the car's reachability, not a Mate fault. Genuine cloud-unreachable or auth/PIN errors still show clearly in red.

## [1.21.5] — 2026-06-15

### Fixed
- **No more negative "best efficiency" in Statistics.** A trip recorded with the SoC *rising* (e.g. a trip window mis‑bounded across a charge during an offline gap) produced a negative efficiency, which surfaced as a nonsensical "best efficiency" like −39 kWh/100km. Mate now never stores or displays a negative efficiency (the trip‑finalize/repair paths withhold it, and a one‑time cleanup nulls any already recorded), so the best/average figures stay real.

### Changed
- **Clearer "dedicated account" guidance.** The Leapmotor account Mate uses must be **exclusive to Mate** — never signed into another app, add‑on, Docker container or integration at the same time. Leapmotor allows only ~one active session per account, so concurrent clients evict each other's session, the car goes offline, and you get **missing or inconsistent data**. This is now spelled out in the setup wizard, the README and the Docker Hub page.

## [1.21.4] — 2026-06-15

### Fixed
- **No more phantom "charged from 0%" charges (and false "Recover missed charges" results).** Once in a
  while the car answers a poll without its battery‑% (SoC) field — often a read perturbed by a command
  you just sent (e.g. changing the charge limit). Mate read that missing value as **0%**, so both the
  live detection and the **Recover missed charges** scan could invent a charge "from 0% to your current
  level" (tens of kWh that never happened). Three‑layer fix: a poll with no usable SoC (or SoC 0% while
  the car still reports range) is now treated as *no live data* and skipped at the source; a
  reconstructed or scanned charge whose implied power is physically impossible (a full pack "charged" in
  seconds) is rejected; and a one‑time cleanup removes any phantom charges already recorded plus the
  bogus 0% data points behind them. Real charges that happened while the car was asleep are still
  reconstructed as before.

## [1.21.3] — 2026-06-14

### Fixed
- **Overview charge ETA now shows the real charge limit, not a hardcoded 100%.** While charging, the
  hero card read e.g. *"3h 00m al 100%"* even when the car's charge limit was set to 90% — the target
  percentage was a fixed string. It now shows the **actual configured limit** the car reports
  (*"… al 90%"*, the same value as the Charges page "Charge Limit"). The car already reports both the
  remaining time and the SoC it will stop at — only the label was wrong. The limit is captured by the
  poller from each status read (free — it's in the same response; updates even if you change it from the
  official app) and persisted, and is mirrored immediately when you set it from Mate. Localised IT/EN/FR/DE.
- **Charging animation no longer overlaps the car image.** The plug → flow → battery animation was
  absolutely positioned over the car picture, so on every screen the icons sat on top of the vehicle
  (wheels/body), and in the narrow 3‑column layout (~1024px, e.g. a slim Home Assistant panel) the
  status pill wrapped and split *"al 90%"* across two lines. The animation now sits **below** the car in
  normal flow — it can't overlap the vehicle at any resolution — and the charging pill stays on **one
  line** at every width (verified 320 → 1280px).

## [1.21.2] — 2026-06-14

### Added
- **Manual charge cost — enter what you actually paid.** Public charging is a jungle (flat
  subscriptions, per‑plan Ionity rates, Tesla's monthly fee + time‑of‑use pricing, session/idle fees…)
  and the bill often isn't a clean €/kWh — so a per‑kWh tariff can't model it. A charge's type selector
  now has a 5th option, **✎ Manual**: pick it and type the **real total paid**; it **overrides** Mate's
  table estimate. The effective **€/kWh** (cost ÷ the charge's energy) is shown on the card and feeds
  the trip cost / weighted‑average‑cost (WAC) **exactly like any priced charge** — so the next trip is
  priced from what you really paid. A manual cost is **protected**: the auto‑Home confirm and the
  one‑time energy/cost repairs never overwrite it (the repairs may still refine the *energy*, which only
  sharpens the manual €/kWh). Accepts a comma decimal (`18,45`).
- **Effective €/kWh on every charge card** — each session now shows its real rate (cost ÷ energy) under
  the cost, so you can see at a glance what each charge actually cost per kWh.

> Implements the cost‑precision half of the request in #56. Payment‑method tagging / per‑method spend
> breakdowns stay **out of scope** for Mate (telemetry‑derived cost is in; payment/billing tracking is
> not — as decided in #17).

## [1.21.1] — 2026-06-14

### Added
- **Try the demo from inside Mate — no command line needed.** The setup screen now opens on a simple
  choice — **"Set up my car"** or **"Try the demo"**. Picking the demo turns it on and restarts straight
  into it; an amber banner at the top of every page lets you leave again (*"Exit demo & set up my car"*).
  So a Home Assistant **add-on** user can explore the sample‑data demo with **one click**, instead of
  needing `-e MATE_DEMO=1` on the command line (which still works for standalone Docker). The in‑demo
  restart waits for the container to come back in the right mode before reloading, so it never hangs.
- **Overview — status‑aware quick‑command icons.** The lock / trunk / windows buttons on the car image
  now mirror the live state: **unlocked** highlights amber, **locked** highlights green, an **open trunk
  turns red**, **open windows** highlight violet — the car's state is readable at a glance.
- **Overview — "cable connected / charge complete" state.** When a charge finishes (or pauses) with the
  cable still plugged in, the car no longer just reads *"Parked"*: it shows **"Cable connected · NN%"** on
  the car image (with the plug icon) plus a **"Charge complete"** status, in the teal charge colour. The
  percentage is the real battery SoC; unplugging the cable returns to the normal parked view.

### Changed
- The setup screen leads with the car/demo choice; the Leapmotor account login and the app‑certificate
  step now appear only **after** choosing *"Set up my car"* (with a **Back** link). The in‑app logo now
  uses Mate's car icon — matching the Docker/add‑on icon — instead of the old "LM" placeholder.

### Security
- **Never bundle the app TLS certificate in the image.** The shared app cert (`certs/app.crt` / `app.key`,
  provided by the user at setup) was already git‑ignored and **absent from the published image**, but a
  *local* `docker build` from a working dir that had the files would have copied them in. They're now in
  `.dockerignore` as well, so a local build can never accidentally bundle them.

## [1.21.0] — 2026-06-14

### Added
- **Demo mode — try Mate before configuring anything.** Run Mate with `MATE_DEMO=1`
  (e.g. `docker run --rm -p 4000:4000 -e MATE_DEMO=1 ghcr.io/protossblaster/leapmotor-mate`) and it serves
  a realistic, self‑contained **month of sample data** — weekday commutes, cheap overnight home AC charging,
  a *weekend al mare* with an expensive **DC fast charge**, the blended (WAC) trip costs, battery health,
  vampire drain and the monthly report — with **no Leapmotor account, car or cloud**. Remote commands are
  **simulated** (lock, climate, windows… flip the demo's own state), so the whole UI is explorable and
  interactive, and a **DEMO** badge is shown. **All data is purely demonstrative — nothing is real.**
  Fully gated behind `MATE_DEMO`: a normal install is unaffected (the demo code is inert when the flag is
  off — verified by the full test suite running in normal mode).

## [1.20.2] — 2026-06-14

### Fixed
- **Remote commands no longer get stuck on "Token is invalid" after heavy use.** The account
  TLS certificate Leapmotor issues at login is a short‑lived temp file; once it got cleaned up,
  the poller and the web process could no longer reuse the shared session and re‑logged in on
  **every cycle** — a login storm the Leapmotor cloud then throttled ("Information verification
  failed, please try again later"), so remote commands failed with *Token is invalid*. Mate now
  copies that certificate to a stable file and can re‑create it from the saved session, so the
  shared session survives a vanished temp file and the re‑login storm is gone. (Reported by
  @riri19, #54. Note: the Leapmotor cloud also needs at least ~10 s between remote commands.)

## [1.20.1] — 2026-06-14

### Added
- **Leapmotor B05 battery capacities.** The new B05 hatchback (2026) is now recognised at setup with its
  two European variants — **55.0 kWh usable** (Pro · 401 km WLTP) and **65.0 kWh usable** (Pro Max · 482 km
  WLTP) — instead of falling back to a generic default. The B05 shares the B10's battery pack, so the figures
  match. Affects new setups and the capacity used for energy/efficiency and battery‑health estimates;
  existing installs keep whatever they already configured.

## [1.20.0] — 2026-06-13

### Changed
- **Trip cost is now based on the _blended_ price of the energy actually in your battery
  (weighted‑average cost), not the price of your single last charge.** This fixes a real
  over‑billing on mixed charging: previously, right after a small expensive public/HPC charge,
  **every** following trip was billed at that premium rate — even though most of the energy still in
  the battery came from a cheaper home charge. Now each charge blends into a running average by **how
  much energy it added**, and a trip is priced at that blend. *Example:* a full home charge at
  €0.25/kWh, then a 20 kWh HPC top‑up at €0.75/kWh, leaves the pack at a blended **€0.42/kWh** (not
  €0.75) — so a 20 kWh trip reads **€8.33**, not €15.00. _(Suggested by @riri19, #53; builds on the
  #51 billed‑energy fix.)_

  **How trip costs are calculated from now on — please read, so the numbers make sense:**
  - **A per‑trip cost is an estimate, not an invoice.** You pay at the charger, not per trip, so Mate
    _allocates_ your charging spend across trips. The trip costs will normally sum to a bit **less**
    than what you actually paid: some energy goes to climate, standby (vampire) drain, charging losses
    and regen. **That gap is expected — not a bug.**
  - **The price only moves when you charge** (never while driving): at each charge, new blended €/kWh =
    `(kWh still in the pack × old price + kWh added × this charge's price) ÷ total kWh`.
  - **A public charge counts only once you confirm its cost** on the Charges page (home charges are
    priced automatically). Until you confirm it, your trips keep the previous price and then update by
    themselves the moment you confirm it.
  - **The trip's energy (kWh) is still estimated from the battery %**, so very short or sparsely‑polled
    trips can still read rough. A more precise energy method (from pack voltage × current) is planned
    (#52).
  - The new figure can come out **higher _or_ lower** than before depending on your charge mix — that's
    it being more accurate, not a regression.
- **Overview layout tidied** — the vehicle card and the "last known position" map swapped places for a
  cleaner flow.

### Added
- **Redesigned vehicle card on the Overview.** The car photo now doubles as a live panel: **doors
  locked/unlocked** and **open‑windows count** overlaid on the image, **quick command buttons**
  (unlock · lock · trunk · windows, colour‑coded) below it, and — while charging — an **animated energy
  flow** with live **kW · % · time‑to‑full**. It now falls back to a placeholder when the cloud photo
  isn't available, instead of the whole card disappearing.
- **"New section" badges in Settings.** When a future release adds a Settings section, it shows a
  **NEW** badge on its header until you open it once — so a new option isn't missed in the changelog.
  (No section is flagged in this release; the mechanism is ready for the next one.)

## [1.19.3] — 2026-06-13

### Fixed
- **The account TLS certificate now survives restarts (root-cause fix for the vanishing cert).** At
  every login the API writes the account certificate and key as temporary files. They used to land in
  the container's **ephemeral `/tmp`**, which a standalone Docker install (e.g. on a NAS) wipes on
  every restart — so the two files vanished, remote commands failed with *"Could not find the TLS
  certificate file"*, and the poller had to re-login on each restart. They now live on the
  **persistent `/data`** volume (via `TMPDIR`), so they persist across restarts and the session is
  reused without a re-login. The v1.19.2 self-heal stays as a safety net. *(Reported by @riri19.)*

## [1.19.2] — 2026-06-13

### Fixed
- **Remote commands now recover from a missing TLS certificate.** On some setups the account's TLS
  certificate (a temporary file) gets cleaned out of `/tmp`, and a command would then fail outright
  with *"Could not find the TLS certificate file"*. Commands now treat this like any other auth
  hiccup — re-login (which re-creates the certificate) and retry — the same self-heal the background
  poller already had. *(Reported by @riri19.)*
- **Quieter command logs.** A command that fails once and succeeds on retry (a transient stale
  connection or an expired token) no longer logs an alarming `ERROR`; the error level is now reserved
  for a command that actually gives up.

## [1.19.1] — 2026-06-13

### Fixed
- **Trip starts no longer missed after the car sleeps.** When the car was parked and not reporting,
  the poller could back off to a fixed 15-minute cycle and only notice a drive well after it had begun
  (the first sample already at speed, the start of the route cut off). It now keeps polling at **your
  configured cadence** whenever the car or the cloud is briefly unreachable, so the start of a trip is
  caught as soon as data returns. Polling the cloud never wakes or drains the car, and re-login stays
  rate-limited. *(Diagnosed with @riri19, #52.)*
- **"Driving" shown while parked (Home Assistant / ABRP).** The published vehicle state was derived
  from a climate-fan signal, so a fan speed of 3–5 while parked could read as *driving*. It now comes
  from the **gear and speed** — the same inputs as trip detection — so the MQTT `state` sensor and the
  ABRP `is_parked` flag match reality. *(Reported by @riri19.)*
- **Crash-recovery trip distance.** Closing a trip left open by a restart now filters out null-island
  (0, 0) GPS fixes before measuring, so a single stray point can no longer inflate a trip's distance.

## [1.19.0] — 2026-06-13

### Added
- **📆 Monthly Report.** A new **Report** page brings one month of driving, charging and **cost**
  together at a glance: distance, trips, average efficiency and energy used; charging cost, sessions,
  energy charged and average €/kWh; a **Home vs public** split; cost per 100 km, regen and drive time;
  **deltas vs the previous month**; and **daily distance/cost charts**. Move between months with ◀ ▶
  or the dropdown, and see a **map of every trip that month**. The figures match the Statistics
  (driving) and Charges (cost) pages exactly.
- **🔒 Security indicator on the Overview.** The first card now shows a **Security** row (just above
  READY) — green **Active** when the car is locked and its alarm is armed — mirroring Leapmotor's own
  *vehicle security active* signal. *(Suggested by @riri19.)*
- **✅ "Fully charged" badge.** While the cable is still plugged in and the charge has completed, the
  Overview charging card shows a **Fully charged** badge.
- **Battery-health cold cutoff.** A new slider in **Settings → Advanced** sets the temperature below
  which cold charges are excluded from the State-of-Health estimate (a cold LFP pack reads low). Default
  15 °C; set it to 0 to include every charge.

### Fixed
- **Privacy of the shareable diagnostics bundle.** The exported diagnostics now also redact three things
  that could leak when a bundle is posted publicly: the remote-control **`operatePassword`** (it was
  printed in clear in the web log), the **VIN where it appears lowercase inside the Home Assistant MQTT
  discovery topic**, and **exact GPS coordinates** in the trip logs (truncated to ~10 km). The actual
  MQTT topic, the database and live logging are unchanged. *(Reported by @riri19.)*

## [1.18.1] — 2026-06-12

### Fixed
- **Trip cost on a wallbox install.** A trip's cost is derived from the €/kWh of the last
  charge before it. That rate divided the charge's cost by the **battery (DC/SoC) energy**, but
  HOME charges are billed on the (larger) **wallbox AC energy** — so the rate, and every trip's
  cost, was overstated by the charging losses (and by more when a charge ended near 100%). It now
  divides by the **same energy the cost was billed on** (AC for HOME, battery otherwise), so a
  trip's €/kWh matches your real tariff. *(Reported by @riri19, #51.)*

## [1.18.0] — 2026-06-12

### Added
- **📍 Charging-station names on charges** *(opt-in)*. Every public charge is tagged automatically
  with the name of the station it happened at — shown as **📍 Station name** on the Charges list and
  on the Overview "last charge" card. The lookup runs in the background, fills in already-recorded
  charges too, and **never looks up home charges**. Enable it in **Settings → Charging stations**
  *(off by default)*. *(Idea: @hubcasale, PR #48 — reimplemented Mate-side over multiple sources.)*
- **⚡ Find charging stations** on the **Navigation** page. A new button maps the public chargers
  around the car: choose the **max distance** and **results per page**, filter **by operator**
  (e.g. Electra, Ionity), and see **AC/DC, power (kW) and live availability** — as map pins and a
  list underneath. Tap a station to set it as your destination and send it to the car's navigator.
- **Multi-source charger search**, with cross-source de-duplication: **OpenStreetMap** and the
  **Italian national registry (PUN)** — both keyless and always on — plus **Open Charge Map** and
  **TomTom** when you add their free API keys *(Settings → Charging stations)*. More sources, better
  coverage; live availability comes from the PUN where available.

## [1.17.1] — 2026-06-12

### Changed
- **🎨 Mate has its own face.** New app icon (a car on a telemetry pulse, in the UI's teal) shown in
  the sidebar and mobile header as "LeapMotor **Mate**", plus a **browser-tab favicon** (there was
  none). The same icon now identifies the Home Assistant add-on in the store. The official Leapmotor
  wordmark is no longer used inside the app — Mate is an unofficial companion and now looks the part.

## [1.17.0] — 2026-06-12

### Added
- **🏠 Auto-assign "Home" to wallbox charges** *(opt-in)*. New toggle in **Settings → Wallbox**:
  charges where your wallbox measured energy are confirmed as **Home** automatically — if *your*
  wallbox saw the energy flow, the charge happened at home. The cost goes through the **same engine
  as a manual confirm**, so flat prices **and time-of-use bands** (including the accurate split
  across band changes) come out identical to tapping the badge yourself — verified on real charges.
  Turning the toggle on also confirms the eligible charges already in your history and tells you how
  many. DC/public charges, reconstructed ones and anything you already typed are never touched, and
  you can still change the type by hand afterwards. Off by default.
  *(Idea: @hubcasale, PR #47 — thank you!)*

## [1.16.14] — 2026-06-11

### Changed
- **⚡ Faster add-on installs & updates.** The Home Assistant add-on now installs a **prebuilt
  image** instead of compiling on your device, so installs and updates are much quicker and lighter
  on the hardware. No action needed — your data and settings are kept across the switch. (This also
  clears two deprecation notices the Supervisor was logging about the old build files.)

## [1.16.13] — 2026-06-11

### Fixed
- **🔌 Impossible charge energy & cost fixed.** A wallbox energy counter that reads ~0 when you plug
  in and then snaps back to its **lifetime total** could log a single charge as tens of thousands of
  kWh — with a matching three‑figure cost — throwing off your charge totals. Mate now rejects a
  physically impossible counter jump, keeps counting the **real** energy after it, and a **one‑time
  cleanup** repairs any such charge already in your history: it drops the bogus wallbox figure and
  re‑prices the charge on the battery (SoC) energy at the same €/kWh. Genuine charges are never
  touched (verified on real data). *(GitHub #46.)*

## [1.16.12] — 2026-06-11

### Fixed
- **🔌 No more phantom charges.** A brief plug / charge‑state blip — e.g. the car re‑evaluating after
  you change a charge **schedule** — could leave a fake "charge" in the log that gained no SoC and
  delivered no energy. These empty sessions are now dropped on the spot, and a **one‑time cleanup**
  removes any already in your history. Strictly empty‑only: a charge with **any** SoC gain **or any**
  wallbox‑measured energy is never touched (verified on real data). *(Reported on Telegram.)*

### Changed
- **⏱️ Charge & trip durations read as hours.** A long session now shows **10h 19m** instead of a bare
  *619 min* — in the Overview "last charge" card, the Charges and Trips lists, and the trip detail.

## [1.16.11] — 2026-06-11

### Fixed
- **🎛️ Wallbox picker no longer floods with home power sensors.** On a charger surrounded by lots
  of home‑energy sensors (e.g. a V2C/Trydan with whole‑home monitoring), the role dropdowns could
  list *every* `power` entity in the house. The device filter now anchors on the **power + energy**
  roles (the ones auto‑detection nails reliably), so a secondary role that mapped off‑device — e.g. a
  max‑current control falling back to a household `number` — can no longer collapse the narrowing;
  each dropdown again shows only the wallbox's own sensors. *(Reported on Telegram.)*

## [1.16.10] — 2026-06-11

### Changed
- **🔋 Sharper Battery health (SoH) trend.** The state‑of‑health estimate no longer follows the
  seasons: **cold charges are shown but excluded** from the figure (an LFP pack reads low when cold,
  so a winter session isn't real ageing — threshold configurable via `soh_temp_min_c`, default 15 °C),
  and **charges that end near 100% weigh the most** (the BMS recalibrates the SoC there, so their SoC
  delta — and the estimate — is the most trustworthy). The trend chart gains a **Time / Distance
  toggle** to separate calendar ageing from cycle (per‑km) ageing; each point now carries its battery
  temperature and odometer, and excluded points appear faded with the reason in the tooltip. The
  *Settings → "use measured" capacity* value benefits from the same cleaner estimate. EN/IT/FR/DE.

## [1.16.9] — 2026-06-11

### Added
- **🔓 Log out / change Leapmotor account.** A new **Log out** button in *Settings → Vehicle* clears
  only the stored login and re‑opens the setup wizard so you can link a **different Leapmotor
  account** — without losing anything. Your trips, charges and positions are kept (they're tied to
  the car's VIN, so the same car carries straight over) and the shared app certificate is untouched.
  The poller re‑authenticates as the new account automatically. (Asks for confirmation first.)

### Changed
- **🗺️ Map tiles now load in privacy‑strict Firefox.** OpenStreetMap blocks tiles that arrive without
  a `Referer`, which some Firefox setups (strict tracking protection, private windows, hardened forks
  like LibreWolf/Mullvad) strip — so the map showed "Access blocked" tiles. The tile layers now send
  the page origin explicitly (`referrerPolicy`), fixing it on every map (Map, Overview, Trip detail,
  Navigation). Chrome was unaffected and stays the same.
- **🎛️ Wallbox sensor picker can't be mis‑mapped by unit.** Each role's dropdown now offers **only
  sensors of the right unit** for the two that feed the calculations — **Charging power** lists only
  kW, **Session energy** only kWh — so a kWh meter can no longer be mapped as power (or vice‑versa),
  which used to silently corrupt the stored power and cost data. A choice you already saved is never
  hidden, the other roles (whose unit varies by wallbox) stay unfiltered, and **Show all entities**
  bypasses it for non‑standard setups.
- **🏷️ Precise wallbox field names with units.** Every mapping field now states its unit — *Charging
  power (kW)*, *Session energy (kWh)*, *Charging speed (km/h)* — and the mislabeled current control is
  fixed from "Wallbox power control" to **Max charging current (A)** (it sets amps, not power). *Max
  available* is clarified as **kW or A** since V2C/Pulsar report it in amps. EN/IT/FR/DE.
- The *Vehicle* settings card now opens by default so the new Log out button is easy to find.

## [1.16.8] — 2026-06-11

### Changed
- **🪗 The Settings page is now a tidy accordion.** Every section is collapsible (the same pattern as
  the old *Advanced* card) and **starts collapsed**, so instead of scrolling past long, always-open
  cards you get a clean list of titles and open only the one you need. Each card **remembers its
  open/collapsed state** (saved server-side, shared across devices), and the integration cards (ABRP,
  Wallbox, MQTT) show their **connection status right in the header** even while collapsed. Cards are
  balanced **5/4/4** across the three columns so the page no longer leaves big empty gaps.
  *(Suggested by a user on Telegram.)*

### Fixed
- The *Advanced* card now actually remembers whether you left it open — its key was missing from the
  save allowlist, so toggling it never persisted before.

## [1.16.7] — 2026-06-11

### Changed
- **🔌 Readable wallbox entity dropdowns.** Long Home Assistant entity names were truncated in the
  two-column mapping grid — and the truncated part was exactly the word that tells roles apart
  (*Voltage* / *Power* / *Energy*). The pickers are now **one per line** (full width), and **hovering
  shows the whole name** as a tooltip — both on each option in the open list and on the closed box,
  where it reflects the entity you've mapped (including its full `entity_id`, handy when two sensors
  look almost identical). *(Suggested by a user on Telegram.)*

## [1.16.6] — 2026-06-10

### Added
- **🔌 Wallbox setup now explains itself (#44).** Every entity-mapping field has a short hint under it
  saying exactly what to pick — the *type* of Home Assistant entity (a `sensor`, or a `number` for the
  current control) and its *unit* — plus a line at the top explaining how auto-detection works and what to
  do if your charger isn't listed (tick "Show all", or add its name to the detection keywords). EN/IT/FR/DE.

### Fixed
- **🔍 V2C Trydan chargers are detected automatically (#44).** Added `v2c`/`trydan` to the detection
  keywords (matched on the entity id, which doesn't change with your HA language), so the picker fills in
  on its own. Solar and house-power sensors (e.g. "Energia fotovoltaica", "Alimentazione domestica",
  "Consumo appartamento") — which are also `power`/`energy` entities — are now kept out of the charging
  roles so they can't be mapped by mistake.

## [1.16.5] — 2026-06-10

### Fixed
- **🦇 No more scary “9 %/day” vampire-drain bars (#41).** The chart normalises every parked window to
  %/day — so a single 0.1%-resolution SoC step over a short park got multiplied into a huge bar (a real
  case: −0.4% over 1.1 h → “9.1 %/day”, against a true error band of ±4.4). Short or still-running parks
  are still recorded, but now render as **pale bars** with the ± uncertainty in the tooltip and a “low
  confidence (short park)” note; the park still in progress is marked “still parked” with a “…” on its
  date. Long, trustworthy windows stay solid purple. *(The small drop itself is usually genuine: in the
  first hour after a drive the car hasn’t reached deep sleep yet — that’s a few hundred watts, briefly.
  It’s the ×22 extrapolation to a daily rate that was misleading.)*
- **📍 The map no longer drifts back into the sea for UK / west-of-Greenwich cars (#43).** The signed-sign
  fix from v1.15.0 (#30) is still in place, but some cars omit the signed coordinate in certain poll states
  (and a restart — e.g. an add-on update — forgot what it had learned), so Mate fell back to the *unsigned*
  value and a Lichfield car jumped out to the North Sea again. Mate now **remembers which hemisphere your
  car is in** and re-applies it whenever a poll sends only the unsigned coordinate — and it persists that
  across restarts. East-of-Greenwich cars are unaffected.

### Changed
- **The “typical idle drain” headline is now time-weighted** — total SoC lost ÷ total parked time, across
  *all* parks of at least an hour **including the ones that lost nothing** (which the old median quietly
  skipped, so it overstated drain: a real install read 1.9 %/day where the honest figure is 0.8). If the
  number drops after updating, that’s the correction — not a change in your car.
- **“Average efficiency” now means the same thing on every page (#42).** The Statistics overview showed a
  plain average of each trip’s efficiency, while the Trips page showed a distance-weighted one — so the
  same trips read e.g. 16.6 vs 19.1 kWh/100 km, and the Statistics figure didn’t even match its own
  “energy used ÷ distance”. Both pages now use the distance-weighted value (total energy ÷ total distance),
  which is the physically correct fleet average and what other EV apps report.

## [1.16.4] — 2026-06-10

### Fixed
- **The “Delete trip” button actually deletes now.** It had never worked since it shipped (v1.13.0): its
  relative URL resolved against the app root, so the request went to a route that doesn’t exist and the
  button silently did nothing. If you've ever pressed it and the trip stubbornly stayed — that was this.
  Verified end-to-end, standalone and behind Home Assistant ingress.

## [1.16.3] — 2026-06-10

### Added
- **🔘 “Door Lock Toggle” — a switch you can put on one button (#38).** Launcher widgets (like Samsung’s
  Home Assistant widget) force a *fixed* action on lock-type entities, so the Door Lock entity couldn’t
  work as a single lock/unlock button. There’s now also a **switch** entity (ON = locked): widgets *can*
  toggle a switch, so one tap locks, the next unlocks. The lock entity stays for dashboards. *(Validated
  end-to-end on a real MQTT broker.)*

### Removed
- **The separate Lock / Unlock buttons in Home Assistant** — fully redundant now that the Door Lock entity
  (state + both actions) and the Door Lock Toggle switch exist; they disappear automatically on update. If
  you had them on a dashboard, swap in **Door Lock**. Automations that publish the raw `lock` / `unlock`
  MQTT commands keep working unchanged.

### Fixed
- **🅿️ A few-metres manoeuvre is no longer logged as a “1 km” trip.** The odometer reads in whole km, so a
  short driveway shuffle that happened to cross a km boundary was recorded as a 1.0 km trip (a real case:
  24 m logged as 1 km). On that ambiguous reading Mate now cross-checks the GPS track and drops the phantom
  hop; a **one-time repair** corrects such historical trips to their real distance (nothing is deleted).
- **✋ Closing asks for confirmation too.** Boot, windows and roof shade asked “are you sure?” only when
  *opening*; closing fired immediately — but a remote close can pinch a hand (or just be an accidental
  tap). Both directions now confirm when parked; while driving you still go straight to the “vehicle in
  motion” notice.
- **📅 Dates in your language.** The recent-trips list and the trip page title now show “10 giu 2026” (or
  “10 Jun 2026”, “10 juin 2026”…) instead of the raw “2026-06-10”.
- **📱 No more giant wrapped text on phones.** The efficiency figure (“20 kWh/100km”) rendered whole at
  headline size, wrapping mid-value on the trip page and overflowing the Trips summary tile; it now shows
  a big number with a small unit, like every other stat. The trip-page date also stays on one line.

## [1.16.2] — 2026-06-10

### Fixed
- **No more pointless “are you sure?” prompt when a control is blocked by motion.** Pressing lock / boot /
  windows / sunshade **while driving** used to ask for confirmation first and only *then* show the “vehicle
  in motion — disabled” notice. Now, while moving, the press goes straight to the notice. **Parked behaviour
  is unchanged** — those controls still ask for confirmation, so an accidental tap can’t open a parked car
  you’re not standing next to.

## [1.16.1] — 2026-06-10

### Fixed
- **Controls the car locks while moving no longer misbehave — sunshade, boot, windows and door-lock.**
  These can't be operated in motion (the official app shows the same notice), and the sunshade's state
  signal is unreliable at speed — which could make the **Panoramic Roof** tile briefly read *“closed”*
  while it was actually open. The Commands page now shows a **“car in motion” banner** over those controls
  while driving, and pressing one returns a clear **“Vehicle in motion — … disabled”** notice instead of
  firing a command the car would only reject. Climate and comfort controls are unaffected — they work
  while driving.

## [1.16.0] — 2026-06-10

### Added
- **⚙️ Advanced settings** (Settings → Advanced, collapsed by default). Three tunables for edge cases, with
  sane defaults and a Reset: the **missed-charge SoC threshold** (the battery-% jump that counts as a charge
  Mate missed while the car was asleep), the **vampire-drain noise floor** (parked SoC drops smaller than this
  are treated as sensor noise), and the **AC/DC power threshold** (raise it if you have a 22 kW AC wallbox so
  its sessions aren't labelled DC). “The defaults suit most users — change these only if you know why.”
- **🔎 Recover missed charges** (Settings → Diagnostics). A one-time scan of your history for charges that
  happened while the car was asleep, *before* automatic detection existed, and were never logged — it **shows
  you what it found before adding anything**, and is safe to re-run (no duplicates).
- **🔋 Battery capacity is now editable** (Settings). Pre-filled per model; edit it if yours differs, or click
  **“use measured”** to adopt the value Mate worked out from your own charges. Changing it never rewrites past
  charges, and your battery-health % keeps measuring against the original spec.
- **🔒 Single “Door Lock” toggle for Home Assistant.** A proper MQTT *lock* entity that shows the locked state
  **and** locks/unlocks in one tap — so it fits as a single dashboard or phone front-screen button. The
  separate Lock/Unlock buttons stay for anyone already using them.

### Changed
- **🔋 Battery capacity defaults are now the usable (net) figures**, not the gross pack — T03 36.0, B10 Pro
  55.0 / Pro Max 65.0, C10 69.9 / 81.9 kWh. Existing installs keep whatever they had configured (nothing is
  silently changed); the new editable field + “use measured” let you fine-tune to your own car.

### Fixed
- **🔄 Command tiles no longer get stuck after a lock / trunk / climate command (#34).** The command worked,
  but the few-seconds-later check read the cloud before the car had reported the new state, decided the command
  “hadn’t taken”, and saved that stale reading — so the tile (and even a page reload) kept showing the old
  state until you tapped again or hit Refresh. Mate now **waits for the car to actually confirm** the change
  (and never saves an unconfirmed reading in the meantime), the tile flips instantly on the first tap, and
  back-to-back commands can’t clobber each other.

## [1.15.0] — 2026-06-10

### Added
- **🔋 Battery card on the Charges page.** Charge percentage and range now live right where you watch a
  charge happen — no more flipping back to Overview. The card sits between the live-charging panel and the
  charge limit, refreshes every 30 s while you watch, and its battery bar carries a small **marker at your
  charge-limit position**, so you can see at a glance how far the charge will go. (Overview keeps its own
  card, nothing moved away.)
- **🔄 OTA-update indicator (Overview).** Mate now checks the car's message inbox for a pending vehicle
  software update and shows it on the Overview card — so you know an OTA is waiting without opening the
  official app.
- **⬆️ Mate self-update badge.** Mate checks GitHub (every 6 h, in the background) for a newer release and
  shows a small badge next to the version number when one is available — handy for standalone-Docker users
  who don't get Home Assistant's update prompts.

### Changed
- **🔌 The "Unlock cable" button moved to the Charges page** (inside the Charge-limit card, with a short
  description). It's a charging action, so it now lives with the other charging controls instead of the
  Commands page. Same command, same confirmation prompt — and the Home Assistant / MQTT button is unchanged.

### Fixed
- **🌍 West-of-Greenwich cars were plotted in the sea (#30).** The cloud reports the GPS longitude in two
  signals — one **without its sign**, which is the one Mate read: fine for most of mainland Europe (east of
  Greenwich), but a UK car at 1.9°W was mapped at 1.9°E, in the North Sea. Mate now reads the **signed**
  coordinate pair (with the old ones as fallback). Thanks @BatterBits for capturing the raw signals that
  cracked it — positions recorded before this fix keep the old sign; the map is correct from the first poll
  after updating.
- **⚡ Charge energy was over-stated ~15% on charges ending at 100% — the "107% efficiency".** The car's BMS
  *snaps* the displayed battery % to 100 with zero energy actually delivered in the very moment charging
  stops (a top-of-charge recalibration), inflating the ΔSoC-based energy estimate — which became visible as
  an impossible ">100% efficiency" next to the measured wallbox figure. Verified against the integrated
  charging-power telemetry (the true AC→DC efficiency is ~90%): Mate now anchors the energy of 100%-ending
  charges to the last battery % seen *while still charging*, a **one-time automatic repair** recomputes the
  affected historical charges on first start after updating (costs billed on the wallbox counter are
  untouched; DC-estimated costs are rescaled at your original price), and an impossible efficiency is never
  displayed again. Mid-range charges were verified byte-identical before/after — they were always correct.

## [1.14.0] — 2026-06-09

### Added
- **🩺 Diagnostics card (Settings → Diagnostics).** A one-stop place to grab everything we need when
  something goes wrong, so you don't have to dig through container / Home-Assistant add-on logs. It shows a
  read-only system snapshot (Mate version, model, masked VIN, DB size + row counts, last poll, which
  integrations are on), lets you view the recent **poller / web logs** and the car's current **raw signals**
  inline (with a Copy button), and a **Download diagnostics** button that bundles the parts you tick into one
  `.txt` you can attach to a GitHub issue. **Privacy by design:** the downloadable logs always mask your
  personal info — VIN, credentials and e-mail addresses — and never include GPS; the raw-signals view (which
  does include your location) is a separate, explicit action.
- **📏 Units: Metric / Imperial UK / Imperial US** (Settings → Units). Distances, speeds, temperatures and
  tyre pressures now display in your chosen system — **km/°C/bar**, **miles + mph but °C** (the UK), or
  **miles + °F + psi** (the US). It's **display-only**: your stored data always stays metric, so you can
  switch back and forth any time with nothing lost.

### Fixed
- **🔌 Charges missed while the car was asleep are now recorded (#29).** A home charge that started and
  finished while the car was offline/asleep to the cloud was never seen "live" and so was lost entirely.
  Mate now notices the battery-level jump and **reconstructs the charge** from it (marked *auto-detected*),
  instead of dropping it.
- **🛞 Tyre pressures were shown on the wrong wheels (#32).** The wheel order inherited from the upstream
  library was wrong for the B10; corrected (cross-checked on two real cars against the official app), so each
  pressure now matches the right corner.
- **🔄 The Refresh button is now reachable on phones.** On small / portrait screens it was hidden; it now sits
  in the mobile header, always one tap away.

### Changed
- **Doors and seats are now labelled by physical position** (front-left / front-right …) instead of
  driver / passenger. This reads correctly on both left- and right-hand-drive cars (the old labels were
  inverted for RHD/UK vehicles).

## [1.13.0] — 2026-06-09

### Added
- **🔗 Manual trip merge.** A journey that a short, non-charging stop split into two (or more) separate
  trips can now be joined back into one. On the Trips page, the **🔗 Merge** toggle draws a connector
  between every pair of adjacent trips that can be joined; a **gap slider** (5–90 min, default 30) widens
  or narrows which pairs qualify, live. A pair is offered only when the second trip starts within the gap
  **and** at no higher SoC than the first one ended — a SoC rise means you charged in between, so those
  (e.g. the legs of a long road trip) are never merged. Clicking a connector shows a preview (combined
  route + distance, energy and *driving-only* duration) before you confirm. It's **fully reversible and
  non-destructive**: a merged trip carries a 🔗 badge and an **Unmerge** button, and splitting it restores
  the originals exactly. Distance/efficiency are recomputed over the whole trip; the stop time is excluded
  from the duration (and shown separately).
- **🔄 Refresh button.** A manual "Refresh" at the top of the sidebar pulls the car's current state from
  the Leapmotor cloud right away instead of waiting for the next poll. Mate still reads **passively** (it
  never wakes the car or drains the battery), so this just skips the wait when the car is already awake; a
  sleeping car keeps its last reported state until it next wakes. The README now spells out that Mate
  isn't real-time — it polls about every 30 s while parked and 10 s while driving.

### Changed
- **Stationary discharge (vampire drain) now catches slow-draining cars.** The detection threshold dropped
  from 0.5 % to **0.2 %** of SoC per parked period, so cars that lose little while parked — and shorter
  stops — now show up too, instead of being filtered out as sensor noise.

## [1.12.0] — 2026-06-09

### Changed
- **Home charge cost is now measured from the wallbox's own energy counter — not estimated.**
  When a wallbox with a kWh counter is paired, Mate samples the counter all through the charge and bills
  the **energy it actually added** — the sum of the counter's increases over the session, i.e. the exact
  AC energy the wallbox delivered (conversion losses included). It's **reset/race-safe**: a per-session
  counter that zeroes mid-charge is handled just like a lifetime total, no matter when it resets relative
  to a poll. The charge card now leads with the **🔌 wallbox (billed)** kWh and shows the **🔋 in-battery
  (DC, from SoC)** energy beneath it with the AC→DC efficiency; the cost is plainly *wallbox kWh × price*.
  With no wallbox counter (or for public charges) the **battery (SoC) energy × price** is billed instead.
  The previous method integrated the fluctuating power curve, which under-counted on short/sparse sessions
  and produced costs that didn't add up — that estimation is gone; instantaneous power now feeds only the
  chart. Day / month / year and lifetime energy totals sum the billed energy too, so cards and totals agree.
  *(Charges recorded before 1.12.0 keep their earlier value and **can't be recomputed** with the new
  method — the wallbox counter readings weren't captured during those past sessions. You can delete an old
  session with its 🗑 button if you prefer.)*

### Added
- **Delete a charge session** — a 🗑 button on each charge card (with confirmation), mirroring the
  existing delete-trip action. Day / month / lifetime totals recompute automatically.
- **Pages keep themselves fresh.** The Charges page reloads the instant a charge finishes, so the new
  completed session shows up without a manual refresh; the live/data pages (Vehicle, Wallbox, Battery,
  Statistics, Trips, Commands) auto-refresh while idle — never while you're filling in a form, and your
  scroll position is kept.

### Fixed
- **A finished charge no longer stays "charging" for several minutes.** On the B10 the car's plug flag
  (signal 47) latches on after an AC charge and clears only minutes later — so a charge session could
  linger open long after charging had finished (and even after the cable was unplugged), inflating its
  window. Mate now derives "plugged in" from the charge-connection signal (1149), which drops as soon as
  the session ends, so the charge closes promptly and its window is accurate.
- **GPX export of a single trip downloaded an empty file.** The download link resolved to the wrong URL
  (a 404) under the app's base path; it's now linked correctly (Home Assistant ingress included).

## [1.11.18] — 2026-06-08

### Added
- **Vampire drain** on the Battery page — how much charge the car loses while **parked and not charging**.
  Computed automatically from the telemetry Mate already logs: it groups the parked-idle periods (no driving — by
  speed *or* an odometer change — and no charging), measures the SoC each one lost, and shows it as a per-period
  bar plus a "typical %/day" figure. Periods under an hour or with a sub-0.5 % drop are treated as sensor noise.
  No setup, no input.

### Changed
- **Sending a command no longer risks logging you out of the official Leapmotor app** (on a shared account). When a
  command hits an expired token, Mate now **refreshes the token** (keeping the same session) instead of doing a full
  re-login — a full login is what the cloud treats as a new device session and uses to evict your phone. A full
  re-login is still the fallback if the refresh can't recover (and still self-heals a missing certificate). The
  background poller already worked this way; this brings the command path in line.

## [1.11.17] — 2026-06-08

### Fixed
- **A home charge could show an impossible cost** (e.g. 10.3 kWh billed at 11.07 € — GitHub #24). Home charges
  are billed on the real AC energy the wallbox delivers, which is read from the charge's power curve. The 1.11.16
  fix capped the *displayed* charging window and the split-cost query so an offline-interrupted/overlapping charge
  couldn't absorb a later charge's power — but the **power-curve read used for the AC energy was left un-capped**,
  so for such a charge the AC energy (and therefore the cost) still leaked in a later charge's wallbox power. That
  read is now capped at the next charge's start too, so the AC energy and cost stay bounded to the charge itself.
  The same cap was applied to the battery-health energy integral.
- **An implausible wallbox AC reading is no longer shown or billed.** If the AC energy comes out more than twice the
  DC energy into the battery (physically impossible — real efficiency is ~75–95 %), or can't be validated because the
  DC figure is zero/missing, it's discarded and the cost falls back to the battery (DC/SoC) energy. This guards
  against a mis-mapped Home Assistant entity (e.g. a cumulative-kWh meter mapped as the wallbox *power* sensor, which
  showed thousands of kWh at ~0 % efficiency).
- **The AC/DC comparison energy now skips gaps over 15 min**, consistent with the time-of-use cost split, so a long
  pause inside a single charge no longer inflates the AC energy (and the cost billed on it).

> **Note:** a charge's cost is frozen when you confirm its type. To recompute an already-recorded charge with the
> fix, just re-select its location (e.g. "🏠 Home") on the Charges page.

## [1.11.16] — 2026-06-08

### Fixed
- **Two charge rows for one plug-in could share the same "Charged HH:MM → HH:MM" end time** (GitHub #23).
  If the cloud API went unreachable mid-charge (3 errors → OFFLINE) and recovered, the poller opened a
  *second* charge row instead of resuming the open one, leaving the first as an orphan. When that orphan was
  later closed, its `ended_at` was set to the latest position — bleeding past the next charge so both rows'
  active-power windows (and split costs) inherited the later charge's last power sample. Fixed at the source
  (the `recorder` no longer opens a charge while one is already open; `close_orphan_charges` caps the orphan's
  end at the next charge's start) **and** defensively in the web layer (the power-window/cost queries are
  capped at the next charge's start, so already-recorded overlaps display and cost correctly too).
- **Time-of-use "split" cost was distorted for a charge with a long pause between two power bursts.** The gap
  between bursts was integrated as a phantom constant-power interval priced at the gap-start's band, skewing
  the weighted-average price. Gaps over 15 min are now skipped (matching the energy-integration guard).
- **A day-restricted off-peak band crossing midnight no longer drops its after-midnight hours to the base
  price.** A "23:30 → 07:30" band on selected days now correctly prices the early-morning hours as belonging
  to the previous (selected) day.

### Changed
- **Re-confirming a charge's type now refreshes its cost immediately** (instead of blanking it until reload)
  and adds a tooltip showing whether the cost was billed on the wallbox **AC** energy or fell back to battery
  **DC** (e.g. when the wallbox AC history is no longer available). The DC fallback is also logged.

## [1.11.15] — 2026-06-08

### Fixed
- **Trips were no longer saved on cars that don't report GPS.** The 1.11.10 odometer fix falls back to the
  GPS track for distance; if the car reports neither a valid odometer nor any GPS fixes, the distance came
  out as 0 and the trip was dropped as a "< 0.5 km short hop". Such trips are now **kept** with an unknown
  distance (time, SoC and energy preserved); only genuinely-measured sub-0.5 km hops are dropped. The
  one-time odometer-repair migration is likewise fixed to keep (not delete) GPS-less trips. (Regression in 1.11.10.)
- **Wallbox max-current slider snapping back to 0 after you moved it.** Setting the value re-read the entity
  immediately, but HA's `number.set_value` is async and a device-backed wallbox often still reports the
  old/idle value for a moment, so the slider jumped back. It now keeps the value you set (optimistic), and
  the real reading reappears on the next page load.

### Changed
- **Clearer label for the wallbox power control in Settings.** The wallbox entity-mapping role previously
  labelled "Max current" is now **"Wallbox power control"** (with a hint that it's the adjustable charging
  current/power `number` entity), to make it easier to map the right entity and avoid mistakes.

## [1.11.14] — 2026-06-08

### Changed
- **Home charges are billed on the wallbox's AC energy.** When a wallbox is configured, a **HOME** charge's
  cost now uses the **AC energy the wallbox actually delivered** (what you pay the utility, including AC→DC
  conversion losses) instead of only the DC energy that reached the battery. Public/away charges and setups
  without a wallbox keep DC billing (the only figure available there). Per the "new charges only" rule,
  already-costed charges are unchanged — re-confirm a charge's type to recompute it. Thanks @riri19 (#23).

## [1.11.13] — 2026-06-08

### Fixed
- **"Charged" (actual charging window) showed the wrong start time.** The real charging window added in
  1.11.12 compared the charge timestamps (which arrive in local time) against the UTC position log, so the
  start could be shifted by the timezone offset (e.g. 20:48 instead of 18:48) and the line could even appear
  on normal charges. The window bounds are now normalized to UTC before the lookup. (Regression in 1.11.12.)

## [1.11.12] — 2026-06-08

### Changed
- **Charges show the actual charging window for delayed/scheduled charges.** A session is recorded from
  cable plug-in to unplug, so a scheduled/delayed charge folds in the idle time before/after power
  actually flows. The charges list now adds an **"Charged HH:MM → HH:MM"** line with the real charging
  window (first→last power sample) whenever it differs from the plug-in→unplug window, so the displayed
  times match reality. Normal charges are unchanged, and off-peak (time-of-use) cost was already correct
  in the default "Accurate split" pricing (energy is attributed by the real power curve). Reported in #23.

## [1.11.11] — 2026-06-08

### Added
- **One-touch vehicle preparation.** A new **Prepare car** page mirrors the official app's
  "Preparazione del veicolo con un solo tocco": bundle air-conditioning (cool/heat/vent/defrost +
  temperature), front-seat heating/ventilation, steering-wheel heating, mirror heating and an optional
  destination, then run it **now** (cmd 360) or on a **schedule** (cmd 361) with a time + weekdays.
  Scheduled preparations are listed (read from the car), **editable** and individually removable. A
  **"Cancel preparation (all off)"** button turns A/C, seats, steering and mirror heating back off. This
  completes coverage of the B10 app's remote functions in Mate.
- **Delete trip.** The trip detail page now has a **🗑 Delete trip** button (with an explicit
  confirmation prompt) that permanently removes a trip and its GPS track; daily/monthly/lifetime
  totals recompute automatically. Useful for one-off bad data.

## [1.11.10] — 2026-06-08

### Fixed
- **Trip distance could log the car's entire mileage.** When the odometer signal was missing on the
  first poll of a trip, the trip's start odometer was recorded as 0 and its distance became the full
  odometer reading — a few-metre move showing up as thousands of km (e.g. a 3-minute hop logged as
  6441 km), inflating daily/monthly totals and efficiency. Trip distance now trusts the odometer delta
  only when both readings are valid, otherwise falling back to the GPS track, which also ignores
  spurious `(0,0)`/out-of-range GPS fixes. Affected trips already in the database are recomputed from
  their GPS track (or removed if under 0.5 km) automatically on the next start — no action needed.
  (Thanks to the user who reported a 6441 km "trip".)

## [1.11.9] — 2026-06-08

### Added
- **Configurable wallbox detection keywords.** The **Settings → Wallbox** panel now has a field for
  comma-separated keywords used to auto-detect your wallbox entities in Home Assistant. Useful when
  your charger's entities don't match the built-in names (Easee, go-e, KEBA, Pulsar, Feyree…). Leave
  it empty to keep the defaults; custom keywords replace the automatic device-class detection.
  (Thanks to **@hubcasale** — Corrado Gamberoni — PR #22.)

### Fixed
- **Wallbox AC energy / efficiency.** The AC-from-wallbox energy is now integrated with the same
  step-hold resampling used by the comparison chart, so the numeric kWh totals match the chart and no
  longer show impossible efficiency above 100% when Home Assistant logs the wallbox power sparsely.
  (PR #22.)
- Keyword parsing hardened so a delimiter/whitespace-only entry cleanly falls back to the defaults
  instead of disabling detection, and the entity scan reads the setting once instead of per-entity.

## [1.11.8] — 2026-06-08

### Added
- **Climate pre-conditioning scheduling.** The **Scheduling** page now also writes the **climate
  schedule** (cmd 171) on the B10 — five quick presets (**Quick Cool / Quick Heat / Ventilation /
  Defrost / Auto**), time, days of the week, and (for ventilation / auto) a target temperature on a
  slider; cool/heat lock to their preset temperature. The earlier "the B10 rejects the climate write
  (code -2)" turned out to be a stale/expired `start_time`, not a blocked endpoint — Mate now anchors
  the start to the next occurrence, so the write works. Read / write / edit / cancel all stay in sync
  with the official Leapmotor app. (Reverse-engineered on-car; details shared upstream at
  markoceri/leapmotor-api#5 and kerniger/leapmotor-ha#43.)

### Changed
- **Scheduling UX (charge + climate).** "Active" is now a clear master switch: turning it off resets
  the day selection (the car may charge anytime); the day chips start clean so clicking a day
  *selects* it instead of de-selecting one of seven. Plus an active/inactive badge on both cards, a
  prominent "Mate manages this schedule" note, MDI icons across the page, and an immediate refresh of
  the card after a successful save.

## [1.11.7] — 2026-06-07

### Changed
- **Self-hosted front-end assets (no CDN).** Tailwind, htmx, ApexCharts, Chart.js and Leaflet are now
  served from the add-on itself (`web/static/vendor/`) instead of public CDNs (cdn.tailwindcss.com /
  unpkg / jsdelivr). Benefits: privacy (the UI no longer leaks your IP to third-party CDNs), reliability
  (it keeps working offline or when a CDN is down/blocked), and security (no third-party script in a
  car-control app). No visible change to the interface; verified in both standalone and HA ingress.
  Idea from PR #20 by @LeeTeng2001.

## [1.11.6] — 2026-06-07

### Added
- **Charge scheduling** — a new **Scheduling** page (sidebar). The **charge schedule** (cmd 190) is
  read **and write**: enable, target SoC, start/end window, and a **7-day picker**. Days match the
  Leapmotor app (shown Dom→Sab; stored Monday-first in the `cycles` mask). Writes use read-modify-write
  so the car's existing day mask / repeat / recharge are preserved. (Confirmed on-car.)
  Climate (pre-conditioning) scheduling is **not** included: the B10 cloud rejects the climate-schedule
  write (cmd 171, code -2) even with valid data, so set those in the Leapmotor app for now; we're
  investigating the write path separately.
- **EVCC integration.** The MQTT bridge now also publishes EVCC-friendly `evcc/plugged`, `evcc/charging`
  and `evcc/climate` booleans (`true`/`false`, which EVCC's parser accepts) next to the Home Assistant
  topics — so an EVCC `type: custom` vehicle can read SoC / status / range / odometer. Ready-to-paste
  config in `docs/EVCC.md`.
- **CSV export buttons** on the Trips and Charges pages (links the export that already lived in
  Settings → Export/Backup, plus per-trip GPX).
- **Wallbox — advanced entity mapping** (#21). A **"Show all entities"** toggle in Settings → Wallbox
  lists every sensor/number entity, not just charger-named/typed ones, so foreign-language names or a
  generic energy-meter/relay can be mapped manually. Added FR charger keywords (`borne`, `recharge`,
  `feyree`) to auto-detection (#19).

### Changed
- **Commands page polish.** Uniform tile sizing across all cards; **Quick actions** are now vertical
  tiles with action buttons (Find / Preheat / Unlock cable); stacked columns are equal width on mobile.

## [1.11.5] — 2026-06-07

### Added
- **Unlock charge cable** — unlock the B10 charge port (`unlock_charger`, right 192), promised on #19.
  Exposed both in the **web UI** (new **Quick actions** card, with a confirm prompt) and over **MQTT**
  (Home Assistant discovery button + command handler). i18n in en/it/fr/de.

### Changed
- **Commands page restyle.** A full pass over the page's look & layout:
  - **Icons → Material Design Icons** everywhere, from a single source (`partials/_icons.html` `mdi()`
    macro): automotive glyphs (vehicle lock = `shield-car` green/red, boot = `car-back`, windows =
    `car-side`, roof = `car-convertible`, climate = `air-conditioner`/`snowflake`/`heat-wave`/`fan`/
    `car-defrost-front`, defrost, EV plug, etc.). Card headers and status pills use them too.
  - **Uniform tiles** — fixed icon/label slots + bottom-anchored controls so every tile in a row
    aligns, regardless of label length or control type (slider vs toggle vs button).
  - **Rebalanced two-column layout** — LEFT = Vehicle + Climate, RIGHT = Comfort + Quick actions;
    Comfort widened to 3-up so the columns are height-matched (bottom-left void measured 618px → 72px).
    Collapses to a single column on mobile (vehicle controls first).
  - **Consistent card headers** (icon + title) on every card, including Vehicle.
  - Merged the old Find Car + Battery cards into **Quick actions**; "Preheat" → "Preheat battery".

## [1.11.4] — 2026-06-06

### Added
- **Full comfort controls on the B10** (thanks @kerniger, leapmotor-ha#41, payloads captured from the app):
  - **Heated & ventilated seats** — a per-seat **level slider (off / 1 / 2 / 3)** for driver & passenger,
    colour-accented (heat = amber, ventilation = sky-blue). Payload `{"position":"driver|copilot","level":"0..3"}`.
  - **Heated steering wheel** and **heated mirrors** — on/off toggles on the Comfort card.
- **More climate controls.** The Climate card adds a **Rapid Ventilation** tile and a **temperature stepper
  (18–32 °C)** that sets the target and starts the climate (auto cool/heat vs the cabin temp). Everything runs
  through the cars' single climate command (cmd 170).
- **READY indicator.** The Overview battery card now shows the car's **READY / Not Ready** state, from the
  faithful B10 signal `bcmKeyPositionOn3` (1258) — driven only by the physical key/READY.
- **Home Assistant (MQTT).** The new comfort & climate commands (seat heat/vent on/off per seat, steering &
  mirror heating, rapid ventilation) are exposed as model-aware buttons over MQTT discovery.

### Changed
- **Tyre pressure — status label per wheel.** Each wheel tile on the Vehicle page now shows a
  colour-coded **status**: *normal* (green), *low* / *high* (amber), *too low* / *too high* (red),
  next to the bar value. Adds **high-pressure** warnings (the view previously only flagged low). Low
  still uses the vehicle's own TPMS warning (plus a < 2.0 bar floor); high is threshold-based
  (> 3.0 bar high, > 3.3 too high). Translated in EN/IT/FR/DE.
- **Colour-coded icons for doors, windows & roof.** On the Vehicle page the tile **icon** (not just the
  text) now carries the state: **closed = green**, **open = sky-blue** (doors, trunk, windows and the
  panoramic roof) — blue reads as "open", not as an alarm.

## [1.11.3] — 2026-06-06

### Added
- **Working A/C On/Off on the B10.** Turning the climate **fully off** now works on the B10: a new
  **A/C** tile on the Commands → Climate card powers the air-conditioning off (and on). This uses the
  newly-found command (`ac_switch` with `operate=off`, which drives the `acSwitch` signal to 0) —
  discovered by on-car testing. Previously the B10 had no working remote A/C-off and the button was
  hidden; the capability is now re-enabled for the B10 over both the web UI and Home Assistant (MQTT).

### Changed
- **Removed the 1.11.2 "A/C won't fully turn off" notice and the on-press confirmation** — they are
  obsolete now that Mate can fully turn the climate off on the B10.

### Internal
- Reported the B10 A/C-off payload upstream (the library's `ac_off()` sends `operate=close`, which only
  flips the B10 to AUTO; the B10 needs `operate=off`).

## [1.11.2] — 2026-06-06

### Added
- **Climate "turn off" note.** The Commands page **Climate** card now shows a highlighted notice that
  turning a function off returns the climate to its base mode rather than fully powering the A/C off —
  to switch it off completely you use the Leapmotor app or do it manually in the car. (Reflects the
  cloud API's lack of a reliable remote "A/C off"; avoids confusion that the climate "won't turn off".)
- **Confirmation when turning the climate on.** Pressing a climate **On** button now asks for
  confirmation, reminding you that Mate can't fully power the climate off afterwards (use the Leapmotor
  app or the car). The confirm fires only on the *On* action, not when turning a function off.

## [1.11.1] — 2026-06-06

### Added
- **Total energy consumed per trip.** The trip detail now shows the trip's total **kWh consumed**
  (next to the efficiency), so you can compare it directly against the regenerated energy. (#18)
- **Per-trip cost.** Each trip now shows its **cost**, computed from the energy consumed × the
  price per kWh of the last charge before the trip. Currency-aware (formatted with the configured
  currency). (#18)

## [1.11.0] — 2026-06-06

### Added
- **Comfort sensors on the Commands page.** A new **Comfort** card (beside the controls block)
  shows the read-only state of the **heated/ventilated seats** (driver & passenger), the
  **heated steering wheel** and the **heated mirrors** (left & right) — as tiles matching the
  rest of the page, with proper car icons. These reflect what the car reports. They are also
  published to Home Assistant as native **MQTT sensors**.

### Changed
- **Model-aware controls (per vehicle).** Mate now shows only what *your* car actually supports.
  On the **B10**, for example, the over-MQTT **A/C Off** button is hidden, because the Leapmotor
  cloud does not honour a remote full power-off on that model (an open limitation tracked with the
  API maintainers). CORE telemetry — trips, charges, reports, charts — is never affected.

### Fixed
- **Battery card.** The minimum battery temperature now shows correctly as `NN°` (it previously
  rendered a raw label such as `22min_temp`), and the header texts no longer overlap on narrow
  layouts.

### Docs
- Updated the Home Assistant install instructions for the **2026.2 "Apps" rename** (formerly
  "Add-ons"; *Applicazioni* in Italian).

### Internal
- New per-VIN **capability profile** that drives the model-aware UI/MQTT (each feature classified
  working / broken / untested from on-car probing; confirmed-broken non-core features are hidden).
- CI workflow that auto-syncs the add-on repository's version on each published release.

## [1.10.0] — 2026-06-05

### Added
- **Collapsible integration cards (Settings).** The ABRP, MQTT and Wallbox cards now tuck
  their configuration fields behind a chevron, so integrations you don't use stay compact
  and the page is easier to scan. The enable toggle and Save button stay visible, and
  ticking the enable box opens the card automatically. Each card's open/collapsed state is
  saved **server-side**, so it's remembered across reloads, reboots and devices — not just
  in the current browser.
- **At-a-glance status badges (Settings).** Each integration card shows a small status dot
  in its header, visible even when the card is collapsed. MQTT does a live broker connect
  (*Connected* / *Not connected*), while ABRP reflects its configuration state (*Active* /
  *Not configured* / *Off*) — the same visual language as the existing Wallbox connection
  badge.

### Changed
- **Upgraded the Leapmotor API library to 0.3.1** (from 0.1.4). This brings native
  handling of the T03 status format and the B10→C10 status-path mapping, so Mate no
  longer needs its own patches for those. The vehicle-data parsing is unchanged
  (raw-signal based), so trips, charges and the dashboard are unaffected.

### Internal
- Dropped the bundled `_get_vehicle_raw_status` monkey-patch (the library now maps the
  B10/B11 status path natively) and replaced the hand-rolled last-week energy and
  consumption-rank endpoints with the library's native methods.

## [1.9.0] — 2026-06-05

### Added
- **Battery health page.** A new *Battery health* page estimates your pack's usable
  capacity (and a state-of-health %) over time. For each charge it integrates the
  **measured** energy delivered (∫ voltage × current, the same source as the charge
  power curve) and divides it by the SoC gained — so the estimate tracks real battery
  ageing rather than just echoing the configured nominal capacity. Only charges with a
  meaningful SoC rise and stored telemetry are used; the headline figure is smoothed
  over the most recent charges to cut single-session noise. It's an estimate, not a lab
  measurement.
- **Global map.** A new *Map* page draws every trip as a connected route line (white
  casing + blue line so it stays readable over any road colour) — showing everywhere the
  car has driven — plus your **most-visited places** as bubbles sized by visit count
  (start/end points clustered to ~110 m, no reverse geocoding).
- **SoC & speed profile on each trip.** The trip detail page now charts state-of-charge
  and speed over the course of the drive (replacing the plain speed bar).

### Fixed
- **T03 (EU) vehicles now report live data.** The European API returns the live status
  as named fields at the top level instead of the numeric `signal` block used by
  C10/B10, so the poller saw *"no live data"* forever. Mate now parses both shapes. This
  was the real root cause on the T03 in #9 (the shared-car `carId` retry added in 1.8.2
  was unrelated).

### Notes
- Both new pages read **existing data** (the charge telemetry and trip GPS already
  logged) — nothing new is collected. Very old sessions whose GPS samples were pruned
  simply won't appear.

## [1.8.2] — 2026-06-05

### Added
- **Encrypted credentials at rest.** Your Leapmotor password/PIN and the other stored
  secrets (Home Assistant, ABRP, MQTT and geocoder tokens) are now encrypted in the
  local database with a per‑install key (`/data/secret.key`, auto‑generated; or set
  your own via the `MATE_SECRET_KEY` env var). Existing installs migrate transparently
  on the next start — no re‑login needed. Keep `secret.key` with your backups: a
  database restored without it will ask you to re‑enter the credentials.
- **Optional database pruning** (Settings → Database). Cap raw GPS‑sample storage to
  6/12/18/24 months; the poller prunes old non‑charging samples daily and reclaims
  space — trips and charge curves are always kept. Off by default. The page also shows
  the current database size.
- **Health endpoint** `GET /healthz` (+ Docker HEALTHCHECK): reports whether the poll
  loop is alive, so a wedged poller is visible instead of data silently stopping.
- **Data export & backup.** Settings → Export: download Trips and Charges as CSV and a
  full database backup; each trip page now has a GPX download of its GPS track.

### Changed
- **Faster charge/Wallbox history at scale.** Added a partial index on the telemetry
  table so the charge‑power, time‑of‑use cost and Wallbox queries stay fast as the
  database grows over the years.

### Fixed
- **Shared cars never reported live data** (the poller was stuck on *"Vehicle returned
  no live data"* forever, even while driving). When a car is *shared* to the account —
  exactly what happens if you follow the "use a different account than your phone"
  advice and share the car to that second account — the Leapmotor cloud returns an
  empty status unless the request also carries `carId`. The poller (and the web command
  client) now retry the status request with `carId` when a shared car comes back empty,
  recovering live data automatically. The login line also logs `shared: true/false` to
  make this diagnosable. Reported on the T03 in #9.
- **Regen energy** now scales with the configured driving poll interval instead of a
  hardcoded 10 s.
- **Trip efficiency** is no longer stored as a negative kWh/100km when the battery SOC
  rose over a trip (regen / a cloud SOC blip) — it's withheld instead.

### Security & hardening
- **Secrets are no longer rendered back into the Settings page.** The ABRP / MQTT /
  geocoder fields now show a masked placeholder when set (like the HA token already
  did) and are only overwritten on a non‑empty submit.
- **MQTT commands are thread‑safe.** Remote MQTT commands run on a background thread;
  API access is now serialized with the poll loop and the post‑command "boost" write
  uses its own DB connection, avoiding a rare race.
- **Clear warning on a wrong/missing encryption key** at startup (e.g. a database
  restored without its `secret.key`), instead of an obscure later login failure.
- Added a `.dockerignore` so the local database, `secret.key`, backups and caches can
  never be baked into a built image.
- **Optional login for standalone** (set `MATE_AUTH_PASSWORD`): a password gate with a
  signed, HttpOnly, SameSite=strict session cookie. Off by default and ignored when
  running as a Home Assistant add-on (ingress already authenticates). When enabled it
  also closes the previously open re-`POST /setup` path.

## [1.8.1] — 2026-06-04

### Added
- **Climate over MQTT is now four buttons** — *Quick Cool*, *Quick Heat*, *Defrost*
  and *A/C Off* — mirroring the in-app Commands page, instead of a single on/off
  switch. The old switch only ran the ventilation fan and its OFF did nothing; the
  buttons send the real climate commands. Turning the A/C fully **off** is
  best-effort — the vehicle cloud doesn't reliably honour it (an open issue with the
  Leapmotor API). The deprecated switch is removed from Home Assistant automatically;
  the read-only *Climate* state sensor stays. (Reported in #14.)
- **Malaysian Ringgit (MYR)** added to the display currencies. (Requested in #13.)

### Fixed
- **Recent Trips on the Overview showed UTC** while the Trips page showed local time.
  The Overview now converts trip times to your local timezone too. (#12)

### Changed
- **Quieter logs when the car is asleep.** A parked car in deep sleep is normal; the
  back-off is now logged once instead of repeating every cycle with a climbing
  "after N tries" count that read like an escalating failure.

## [1.8.0] — 2026-06-04

### Added
- **Customizable display currency.** The euro is no longer hardcoded — pick your
  currency from **30 world currencies** (€, $, £, CHF, kr, zł, Ft, ¥, …) in
  **Settings → Language & Currency**. Every cost across the app (Overview, Charges,
  Wallbox, totals) reformats to it, with the correct **symbol placement**
  (e.g. `$12.50` vs `12,50 €`) and **decimal digits** per currency (2 for most,
  0 for yen/forint/won). The number format (decimal/thousands separator) follows
  the selected UI language. The Settings *Language* card is now *Language &
  Currency*, listing currencies by name (e.g. "Euro (€) — EUR"). (Requested in #10.)

## [1.7.1] — 2026-06-04

### Fixed
- **Scary `Poll error: 'signal'` when the car is asleep.** When the Leapmotor cloud
  returned a status without the live signal block — the car in deep sleep / briefly
  not reporting, or a transient cloud hiccup — the poller raised a bare `KeyError`
  and logged it as an `ERROR`, which looked like a crash. It's now handled cleanly:
  the poller logs a clear "vehicle not reporting (asleep/unavailable)" message,
  retries a couple of times, then backs off — and recovers on its own once the car
  reports again. (#9)

## [1.7.0] — 2026-06-04

### Added
- **Charge Prices page with time-of-use tariffs.** A dedicated *Charge Prices*
  page replaces the single price field in Settings. Choose **flat (24h)** pricing
  or **time-of-use bands**: add one or more time windows, pick which **days of the
  week** each applies to (with All / Weekdays / Weekend shortcuts), and set a price
  per **charge type** (Home/AC/DC/HPC) for each. Leave a cell blank to use the base
  price, or enter `0` for free. Each session's cost is computed by splitting its
  energy across the bands it spans using the real power curve. Cost changes apply to
  **new charges only** — a charge's cost is frozen when you confirm its type, so
  later price/band edits never alter past sessions. (Requested in #7.)
- **MQTT "Test connection" button** (Settings → MQTT) — check the
  broker/port/credentials/TLS before saving.

### Fixed
- **MQTT state out of sync after a command.** Lock/unlock/trunk/climate commands
  sent over MQTT executed, but the published state only refreshed on the next poll
  (up to 30 s when parked), so Home Assistant showed stale values. Mate now
  publishes the expected state immediately and triggers a fast re-poll to confirm —
  the same approach the web UI already uses.
- **Inverted lock state in Home Assistant.** A locked car showed up as "Unlocked"
  (Home Assistant's `lock` binary-sensor class is inverted). The lock entity now
  displays correctly; the published topic value is unchanged.

### Changed
- **MQTT topic prefix now scopes the Home Assistant device.** You can run a second
  instance (e.g. a test poller alongside the production add-on, same car) on a
  different prefix without it overwriting the same entities. The default prefix is
  unchanged, so existing installs are unaffected.

## [1.6.3] — 2026-06-04

### Changed
- **Vehicle page redesigned with Material Design icons.** Doors, trunk, windows,
  panoramic roof, tyres and temperatures now use clear, car-specific icons instead
  of emoji (windows even switch between open/closed icons). Self-contained inline
  SVGs — no external icon font.

### Fixed
- **Panoramic roof shows its real state.** The Vehicle page now reads the roof
  position live from the car (signal 1724), consistent with the Commands page,
  instead of relying on the last command / showing "no data".
- **Version number is now visible on mobile** (in the top bar), not only on desktop.

## [1.6.2] — 2026-06-04

### Fixed
- **Wrong clock times for users outside Italy.** The add-on fell back to a
  hardcoded `Europe/Rome` timezone when `TZ` wasn't in the environment (the
  Supervisor only sets the container's local time). Now the UI uses the system /
  Home Assistant timezone, so trips, charges and "last seen" show your real local
  time everywhere.
- **Overview "State" now follows the gear, not just speed.** A stop in traffic
  with the car in Drive used to read as "Parked"; now any gear other than P shows
  "Driving".

### Changed
- **Panoramic roof** shows "Operate first" instead of "No data" when its position
  is unknown — the B10 doesn't report the sunblind's position, so Mate only knows
  it after you open/close it from the app.

## [1.6.1] — 2026-06-04

### Fixed
- **Poller regression (since 1.5.1).** The configurable charge-detection setting
  was applied with a wrong call that raised an error on every poll cycle, so the
  poller stopped collecting data. Fixed — polling, trip and charge detection work
  again. **Update strongly recommended if you're on 1.5.1 or 1.6.0.**
- **Setup PIN field said "6-digit".** The Leapmotor operation PIN is **4 digits** —
  the placeholder/hint and the input length now say 4.

## [1.6.0] — 2026-06-04

### Added
- **Responsive layout for phones and tablets.** On small screens the sidebar
  becomes a slide-out drawer with a top bar + hamburger menu, the content reflows
  to full width, and the maps no longer overlap the navigation. The desktop layout
  is unchanged. Contributed by **@hubcasale** (#6) — thank you!

## [1.5.1] — 2026-06-04

### Added
- **Configurable charge-detection threshold.** The minimum charging current that
  counts as "charging" (below it a plugged-in car is treated as idle) is now
  adjustable in **Settings → Charge detection** (0.5–16 A, default 2 A). Useful for
  low-power / experimental supplies. The poller applies it live, no restart needed.
  Thanks @hubcasale for the suggestion.

## [1.5.0] — 2026-06-04

### Added
- **Navigation page 🧭 — send a destination to the car.** Type a street + city,
  preview it on the map, and push it straight to the vehicle's built-in navigation
  (no PIN). The page also shows the car's **current address** (reverse-geocoded from
  its GPS). Fully translated (EN/IT/FR/DE).
- **Configurable geocoder.** Address lookup works out of the box with a free
  OpenStreetMap-based provider (no key). For better street/house-number coverage you
  can optionally pick a provider and paste an API key in **Settings → Address lookup**
  — **Geoapify** (recommended, free, no credit card, includes house numbers),
  **LocationIQ** or **TomTom**. Any provider error falls back to the keyless lookup.
- **"Free" charge type.** Mark a session as free charging (🆓) — its cost is recorded
  as €0.00.

### Changed
- **Charge-type labels are now language-neutral** — 🏠 Home · 🔌 AC · ⚡ DC · 🚀 HPC ·
  🆓 FREE — so they read the same in every UI language.

### Fixed
- **The "charges to confirm" banner no longer sticks while a charge is in progress.**
  An ongoing session can't be confirmed yet, so it's excluded from the count; only
  finished, unconfirmed charges are flagged.
- **Wallbox power/energy units are auto-detected.** Wallboxes that report power in
  **watts** (or energy in **Wh**) are now normalised to kW/kWh everywhere — the
  AC-vs-DC comparison and the per-session power chart, not just the live panel.

## [1.4.0] — 2026-06-04

### Added
- **German (Deutsch) UI language.** Full translation of the web interface — nav,
  Overview, Trips, Charges, Statistics, Commands, Vehicle, Wallbox, Settings and the
  first-run Setup wizard. Selectable from Settings and the setup screen, and
  auto-detected from the browser language. Requested by the community on GitHub.

### Fixed
- **Month names in the history trees are now localized.** The year → month → day
  breakdowns on the Trips, Charges, Statistics and Wallbox-comparison pages built
  their labels with `strftime("%B"/"%b")`, which is always English regardless of the
  selected language. Month names (full and abbreviated) are now translated for all
  languages (it/fr/de/en) without relying on system locales.

## [1.3.2] — 2026-06-04

### Fixed
- **Tyre pressures were shown on the wrong wheels.** The B10 signal→wheel mapping is
  corrected per markoceri/leapmotor-api's documented signal table — the pressure and
  its low-pressure alarm now refer to the same (correct) wheel.
- **Removed the bogus "outside temperature".** That signal (2101) is actually the
  driver-seat ventilation level; no ambient-temperature signal exists, so the value
  was meaningless. Dropped from the Vehicle page, the MQTT sensors and ABRP
  telemetry (battery/cabin/AC-target temperatures were already correct).

## [1.3.1] — 2026-06-04

### Changed
- Lower the charge-detection current threshold from 3.0 A to 2.0 A so low-power
  home charges (and the tail end of a charge) are still detected as charging. The
  regen detection threshold is separate and unaffected.

## [1.3.0] — 2026-06-04

### Added
- **ABRP (A Better Route Planner) live telemetry** — optional. Enable it and paste
  your personal ABRP token in Settings, and the car's live data (SOC, position,
  speed, power, temperatures…) is forwarded to ABRP for live route planning. Off
  by default; nothing is sent without a token.
- **MQTT → Home Assistant bridge** — optional. Configure a broker in Settings and
  the car is published to Home Assistant via MQTT Discovery as native entities:
  sensors (SOC, range, individual tyres, temperatures, charge…), binary sensors
  (doors/windows/lock/charging), a GPS tracker, command buttons (lock/unlock,
  trunk, find car) and a climate switch. TLS supported. Off by default.

## [1.2.0] — 2026-06-04

### Added
- **Charge type confirmation.** A new charge is no longer silently assumed to be
  "Home": until you set its type it shows a "To confirm" badge (with a "What type
  of charge?" prompt), and the Charges page shows how many are still pending. A
  charge enters the wallbox comparison only once you confirm it as Home.

### Changed
- **The wallbox comparison is now scoped to Home charges**, so it stays correct
  with multiple EVs sharing one wallbox and with public/away charging. History,
  totals and the per‑charge overlay only consider Home charges (a wallbox charges
  one car at a time, so a Home session means this car was on the wallbox);
  public/away and unconfirmed charges are excluded.
- The wallbox **live panel** now shows session metrics only while the car is
  plugged in — otherwise the live reading could be another vehicle on the same
  wallbox. Session cost and max available power are always shown.

## [1.1.1] — 2026-06-04

### Fixed
- **Wallbox in add‑on mode** — the add‑on now correctly detects the Home Assistant
  Supervisor token. On the s6‑overlay base images the Supervisor‑provided
  environment (including `SUPERVISOR_TOKEN`) isn't passed to the service process,
  so the add‑on fell back to the standalone URL+token form and showed "not
  connected". `run.sh` now loads it from the s6 container environment, and logs
  whether the HA API is available at startup.

## [1.1.0] — 2026-06-04

### Added
- **Wallbox integration (Home Assistant)** — optional. Pair a wallbox already in
  Home Assistant to get a dedicated **Wallbox** page with: a live panel (power,
  status, session energy, charging speed, max available power) plus the session
  cost; a control to set the wallbox max charging current; and an **AC‑vs‑DC
  comparison** per charge session — kWh delivered by the wallbox vs kWh into the
  battery, with charging efficiency — as a year/month/day history with an
  expandable power chart. Connects automatically via the Supervisor API when run
  as an add‑on (any external access mode — HTTP, HTTPS, Nabu Casa), or via an HA
  URL + a Long‑Lived Access Token when standalone (self‑signed HTTPS is fine).
  Enable and configure it in **Settings → Wallbox present** (live connection
  status + an entity picker limited to your wallbox's own sensors).

### Changed
- **Trips page redesign** — trip rows show a remaining/used SOC bar, a coloured
  efficiency pill and a route thumbnail; the dashboard gained four summary tiles
  (total distance, trips, average efficiency, regen). Trip distance now comes from
  the odometer delta (more accurate than GPS).
- **Vehicle page** restyled to match the rest of the app (slate cards/tiles).
- **Settings** reorganised into three columns; the Wallbox card stays minimal when
  disabled and reveals the HA connection + an expandable sensor list when enabled.
- Quantities across the UI are shown to at most two decimals, at full precision
  (no over‑rounding).

## [1.0.8] — 2026-06-02

### Added
- **Charging-power chart** in the Charges page. Each session has an expandable
  "Charging power" section that lazy-loads an inline chart of power over time
  (with SOC on a second axis). Power is the same value as the official
  charging-power reading (battery voltage × current) and is kept at full
  precision so the real curve is visible — most useful on DC fast charging.

### Changed
- **Settings layout**: cards now use a masonry column layout, removing the empty
  gap that appeared under shorter cards (e.g. Language) next to taller ones.

## [1.0.7] — 2026-06-02

### Added
- **Language selector in Settings**. The language could previously only be chosen
  in the initial setup wizard, so already-installed users had no way to switch.
  Settings now has a language dropdown (🇬🇧 English / 🇮🇹 Italiano / 🇫🇷 Français);
  changing it saves immediately and reloads the page in the new language.

## [1.0.6] — 2026-06-02

### Added
- **French language** (🇫🇷). The setup wizard now offers three languages — English,
  Italian and French — with three flag buttons and auto-detection of French
  browsers. The whole app is translated: overview, trips, charges, commands,
  statistics, the vehicle page, and both wizard steps (certificate + account login).

### Fixed
- Two certificate-step labels (`app.crt`, `app.key`) were hard-coded in English
  regardless of the chosen language; they are now translated (this also fixes
  Italian, where they were previously shown in English too).

## [1.0.5] — 2026-06-02

### Fixed
- **Poller self-recovery**: if the account TLS certificate temp file vanished from
  `/tmp` (every poll then failed with "Could not find the TLS certificate file"),
  the poller stayed stuck in an error loop indefinitely. It now forces a fresh
  login to re-create the certificate on cert/auth/token/connection errors (rate-
  limited to ~once per minute). Also recovers from auth/token drops.

## [1.0.4] — 2026-06-02

### Fixed
- **Local time in the UI**: trip/charge times were shown in UTC; they are now
  converted to the local timezone (`TZ`, which Home Assistant passes to add-ons
  automatically; standalone Docker sets `TZ` in compose). Added `tzdata`.
- **Trip fragmentation**: a drive was split into many records because a trip ended
  after just ~20s of zero speed. Trip detection is now gear-based and matches the
  HA reference: a trip ends only when gear **P** is held ~1 min (red lights / brief
  stops in gear D no longer split it), and movements **< 0.5 km** are discarded.

## [1.0.3] — 2026-06-02

### Fixed
- **Statistics**: the "Consumption trend (6 weeks)" chart legend showed week
  dates as `MM-DD` (US-style); they are now formatted as `DD/MM`.

## [1.0.2] — 2026-06-02

### Added
- **Vehicle page**: new sidebar page with live tyre pressure (per corner, with
  low-pressure alarms), door and window open/closed states, panoramic roof and
  battery/cabin temperatures — styled as gradient status cards.

### Fixed
- `find_car` was calling a non-existent client method; now driven through the
  registered remote action so it reaches the car.
- Install docs spell out the exact add-on repository URL.

## [1.0.1] — 2026-06-01

### Fixed
- **Home Assistant ingress support**: the web UI now works inside the add-on
  panel. URLs are resolved against the ingress path via `<base href>` (from the
  `X-Ingress-Path` header) and all template/JS URLs are relative; server
  redirects carry the ingress prefix. Standalone is unaffected.

## [1.0.0] — 2026-06-01

First public release.

### Added
- Trip tracking with route map, distance, energy, efficiency and regen.
- Charge logging with AC/DC detection, energy added, power and distribution chart.
- Statistics: driving/AC/other energy split and a 6-week consumption trend (Leapmotor cloud).
- Remote control: lock, windows, trunk, panoramic roof, climate, find car, battery preheat.
- Two-step setup wizard: app certificate (upload/paste) + account login with EU model/battery auto-detect.
- Configurable polling (parked/driving), bilingual UI (EN/IT).
- Home Assistant add-on and standalone Docker deployment.

[1.0.2]: https://github.com/ProtossBlaster/leapmotor-mate/releases/tag/v1.0.2
[1.0.1]: https://github.com/ProtossBlaster/leapmotor-mate/releases/tag/v1.0.1
[1.0.0]: https://github.com/ProtossBlaster/leapmotor-mate/releases/tag/v1.0.0
