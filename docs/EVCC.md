# EVCC integration (via MQTT)

[EVCC](https://evcc.io) can read your Leapmotor straight from the MQTT topics Mate already
publishes — no extra API, no second login. EVCC sees the car's **state of charge**, **plug /
charging status**, **range** and **odometer**, so it can show the vehicle and steer charging
(PV surplus, price-based, target SoC) against live data.

This reuses Mate's existing **MQTT bridge** — the same one that feeds Home Assistant. There is
nothing to enable beyond having MQTT turned on in Mate's **Settings → MQTT**.

## How it works

Mate publishes one retained topic per value under `‹prefix›/‹VIN›/…`. EVCC's generic
**`type: custom`** vehicle reads those topics. Numbers (`soc`, `range`, `odometer`) are read
as-is. For the plug/charging state EVCC needs `true`/`false` booleans (its Go parser rejects the
`ON`/`OFF` that Home Assistant wants), so Mate also publishes EVCC-friendly mirrors:

| Value        | Topic                              | Format        |
|--------------|------------------------------------|---------------|
| State of charge | `‹prefix›/‹VIN›/soc`            | number (%)    |
| Range        | `‹prefix›/‹VIN›/range`             | number (km)   |
| Odometer     | `‹prefix›/‹VIN›/odometer`          | number (km)   |
| Plugged in   | `‹prefix›/‹VIN›/evcc/plugged`      | `true`/`false`|
| Charging     | `‹prefix›/‹VIN›/evcc/charging`     | `true`/`false`|
| Climate on   | `‹prefix›/‹VIN›/evcc/climate`      | `true`/`false`|

`‹prefix›` is the **Topic prefix** from Mate's MQTT settings (default `leapmotor`); `‹VIN›` is
your car's VIN. You can confirm both with any MQTT client, e.g.
`mosquitto_sub -h ‹broker› -t 'leapmotor/#' -v`.

## evcc.yaml

Add the broker (if not already there) and the vehicle. **Replace the VIN** below with yours and,
if you changed it, the `leapmotor` prefix:

```yaml
# Your MQTT broker — the same one configured in Mate's Settings → MQTT
mqtt:
  broker: 192.168.1.10:1883
  # user: youruser
  # password: yourpass

vehicles:
  - name: leapmotor
    type: custom
    title: Leapmotor B10
    capacity: 67            # kWh — set to your battery's usable capacity
    soc:
      source: mqtt
      topic: leapmotor/LFZA5AE24SE008234/soc
      timeout: 1h
    status:                 # derived from plug + charging (EVCC "combined" source)
      source: combined
      plugged:
        source: mqtt
        topic: leapmotor/LFZA5AE24SE008234/evcc/plugged
        timeout: 1h
      charging:
        source: mqtt
        topic: leapmotor/LFZA5AE24SE008234/evcc/charging
        timeout: 1h
    range:
      source: mqtt
      topic: leapmotor/LFZA5AE24SE008234/range
      timeout: 1h
    odometer:
      source: mqtt
      topic: leapmotor/LFZA5AE24SE008234/odometer
      timeout: 1h
    climater:               # optional — true while the A/C / preconditioning is on
      source: mqtt
      topic: leapmotor/LFZA5AE24SE008234/evcc/climate
      timeout: 1h
```

Then assign the vehicle to a loadpoint in EVCC as usual. The `timeout` makes EVCC treat a value
as stale if Mate stops publishing (e.g. the car is in deep sleep and the poll interval stretches
out) — `1h` is comfortable given Mate's offline backoff.

## What this does and doesn't cover

- **Read** — SoC, plug/charging status, range, odometer, climate flag: ✅ live.
- **Charge control** — Mate does **not** expose start/stop charging to EVCC. EVCC steers
  charging at the **wallbox**, not the car, so it doesn't need the car to start/stop. (Mate's
  own **charge limit** and **charge schedule** ARE published as writable MQTT entities — see the
  README — but those drive the *car's* built-in charge plan, which is a different thing from EVCC
  steering the wallbox.)
- **Status** is derived as A (unplugged) / B (plugged, idle) / C (charging) from the two
  booleans, matching EVCC's standard `combined` pattern (same approach as EVCC's `mg2mqtt`
  template).

## Troubleshooting

- **No SoC in EVCC** — check the topic actually carries a value:
  `mosquitto_sub -h ‹broker› -t 'leapmotor/+/soc' -v`. If empty, MQTT isn't enabled in Mate or
  the broker/prefix differs.
- **Status stuck / wrong** — verify `evcc/plugged` and `evcc/charging` publish `true`/`false`
  (not `ON`/`OFF`). The `evcc/*` mirrors are required; the plain `plug_connected` / `charging`
  topics are for Home Assistant and won't parse in EVCC.
- Values only refresh when Mate polls the car; a sleeping car updates slowly by design.
