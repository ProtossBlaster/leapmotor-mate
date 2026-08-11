# LeapMotor Mate

**Trip tracking, charge logging and remote control for Leapmotor vehicles** — a self-hosted
companion (think *TeslaMate* for Leapmotor). Runs as a **standalone Docker container** or as a
**Home Assistant add-on**.

Supported models: **B05 · B10 · C10 · T03** — full‑electric (BEV) only, European spec (the Leapmotor lineup distributed by Stellantis/Leapmotor). Not for REEV / range‑extender versions.

![Overview](https://raw.githubusercontent.com/ProtossBlaster/leapmotor-mate/main/docs/screenshots/overview.png)

## Features

- 🚗 **Trips** — automatic logging with GPS track, distance, energy, efficiency and regen
- 🔌 **Charges** — sessions with energy, cost (flat or time-of-use bands), wallbox integration
- 🔋 **Battery health** — SoH estimate from real DC energy, with temperature-aware filtering
- 🎛️ **Remote control** — climate, locks, windows, seat heating, charge limit, schedules, one-touch "prepare car"
- 🏠 **Home Assistant / MQTT** — full MQTT Discovery: sensors, switches and controls appear automatically
- 🗺️ **Live map**, statistics, vampire-drain insight, ABRP and EVCC integration
- 🌍 English · Italiano · Français · Deutsch · Polski · Nederlands · Português · Español — metric & imperial units
- 🔒 **Self-hosted & private** — your data stays in a local SQLite database, no third-party cloud

## Quick start

```bash
docker run -d --name leapmotor-mate \
  -p 4000:4000 \
  -v "$(pwd)/data:/data" \
  protossblaster/leapmotor-mate:latest
```

Then open **http://localhost:4000** and follow the setup wizard (Leapmotor account login).

> ⚠️ **Use a Leapmotor account dedicated to Mate only** — not one also signed into the official app, another add-on, Docker or integration. Leapmotor allows ~one session per account, so concurrent clients evict each other's session → the car goes offline → **missing or inconsistent data**.

The database is stored in `./data/` (mounted at `/data` in the container).

To update: `docker pull protossblaster/leapmotor-mate:latest` and recreate the container
(or use [Watchtower](https://containrrr.dev/watchtower/) for automatic updates).

## Tags

- `latest` — the most recent release (also tracks the default branch)
- `X.Y.Z` — pinned release versions
- Multi-arch: `linux/amd64` + `linux/arm64`

The same image is also published to GHCR as `ghcr.io/protossblaster/leapmotor-mate`.

## Home Assistant add-on

Running Home Assistant OS/Supervised? Install Mate as an add-on instead (one-click updates,
ingress UI): see the [add-on repository](https://github.com/ProtossBlaster/leapmotor-mate-addon).

## Desktop app

Neither Home Assistant nor Docker? **[MateDesktop](https://github.com/ProtossBlaster/MateDesktop)**
packages Mate as an ordinary **macOS / Windows** application — download, open, same setup wizard.

## Docs & source

Full documentation, screenshots, setup guide (EN/IT) and issue tracker:
**[github.com/ProtossBlaster/leapmotor-mate](https://github.com/ProtossBlaster/leapmotor-mate)**

If Mate is useful to you, you can [buy me a coffee](https://www.buymeacoffee.com/protossblaster) ☕
