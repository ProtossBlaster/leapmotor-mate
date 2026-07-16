"""
Recorder: reacts to state machine events to persist trips, charges, and positions.
"""
import logging
from typing import Optional

from db import Database, _now_iso
from state_machine import State, StateMachine, StateEvent, _PARKED_STATES
from client import VehicleData

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, db: Database, vehicle_id: int):
        self._db = db
        self._vehicle_id = vehicle_id
        self._sm = StateMachine()
        self._active_trip_id: Optional[int] = None
        self._active_charge_id: Optional[int] = None
        self._regen_kwh: float = 0.0
        self._max_charge_kw: float = 0.0
        self._started: bool = False
        # SoC-jump charge reconstruction (GitHub #29): baseline SoC + when we last saw it.
        self._last_soc: Optional[float] = None
        self._last_soc_ts: Optional[str] = None
        self._reconstruct_min_pct: float = 2.0   # min SoC rise to call it a (missed) charge
        # Odometer-jump TRIP reconstruction (#118): baseline odometer. A parked car's odometer never
        # moves, so any jump while parked = a drive we missed offline. Whole-km signal → 1 km floor.
        self._last_odometer: Optional[float] = None
        self._reconstruct_min_km: float = 1.0
        # Timestamp of the last cloud frame (#128) — see process() for what a repeat means.
        self._last_frame_ts: Optional[int] = None

    @property
    def state(self) -> State:
        return self._sm.state

    @property
    def poll_interval(self) -> int:
        return self._sm.poll_interval

    def set_poll_intervals(self, parked: int, driving: int) -> None:
        self._sm.poll_parked = parked
        self._sm.poll_driving = driving

    def set_reconstruct_min_pct(self, pct: float) -> None:
        """Min SoC rise (%) that counts as a charge missed while the car was asleep (Settings).
        Hard floor of 1.0%: below that, parked SoC sensor noise / BMS recalibration jitter
        would invent phantom charges (the value is also clamped in the settings endpoint, but
        guard here too in case the DB was hand-edited)."""
        if pct and pct > 0:
            self._reconstruct_min_pct = max(1.0, pct)

    def _resume_or_close(self, data: VehicleData) -> None:
        """At startup, reconcile sessions left open by a previous run (poller/HA
        restart, crash). If the activity is STILL ongoing, RESUME the open session
        instead of closing it — this avoids fragmenting one physical charge/trip into
        multiple DB records. If it's no longer ongoing, close it (crash recovery)."""
        is_charging = data.charging_status > 0 or data.plug_connected
        is_driving  = data.gear in ("D", "R", "N") or data.speed_kmh > 1

        open_charge = self._db.get_open_charge(self._vehicle_id)
        if open_charge:
            if is_charging:
                self._active_charge_id = open_charge["id"]
                self._max_charge_kw = open_charge["max_power_kw"] or 0.0
                self._sm.state = State.CHARGING
                log.info("Resumed open charge #%d (car still charging)", open_charge["id"])
            else:
                self._db.close_orphan_charges(self._vehicle_id)

        open_trip = self._db.get_open_trip(self._vehicle_id)
        if open_trip:
            if is_driving and not is_charging:
                self._active_trip_id = open_trip["id"]
                self._sm.state = State.DRIVING
                log.info("Resumed open trip #%d (car still driving)", open_trip["id"])
            else:
                self._db.close_orphan_trips(self._vehicle_id)

    def process(self, data: VehicleData) -> None:
        """Called every poll cycle with fresh vehicle data."""
        if not self._started:
            self._started = True
            self._resume_or_close(data)
            # Seed the SoC baseline from the last position on disk so a charge that happened
            # while the poller was DOWN is still caught on the first poll back (GitHub #29).
            prev_soc, prev_ts = self._db.get_last_soc(self._vehicle_id)
            if prev_soc is not None:
                self._last_soc, self._last_soc_ts = prev_soc, prev_ts
            else:                                   # fresh DB → no baseline; skip first-poll reconstruct
                self._last_soc, self._last_soc_ts = data.soc, _now_iso()
            # Seed the odometer baseline too, so a DRIVE during poller downtime is caught on the first
            # poll back (odometer-jump trip reconstruction, #118). None on a fresh DB → first poll just seeds it.
            self._last_odometer = self._db.get_last_odometer(self._vehicle_id)

        # When the car is unreachable (4G dead zone, or the eSIM re-registering on a foreign network
        # at a border) the cloud does not say so — it re-serves the LAST frame it received: identical
        # payload, identical timestamp, poll after poll. While DRIVING that is a lie, because a car
        # that is really moving pushes a fresh frame every single time. Recording the repeats invents
        # data: a flat speed plateau, a route that stands still, regen accrued from a frozen current
        # (#128). Frame identity is the test — it needs no threshold, so a host clock skewed against
        # the cloud (−48s in the wild) cannot fool it.
        stale = bool(data.timestamp_ms) and data.timestamp_ms == self._last_frame_ts
        if data.timestamp_ms:
            self._last_frame_ts = data.timestamp_ms

        # NB: the state machine below must still see every frame, repeats included. If the last real
        # frame said gear P (car parked, then the modem dropped), the SM needs its PARKED_CONFIRM
        # readings to close the trip — hiding them would strand the trip open until the link returns,
        # which is the very bug #128 reports.
        if not (stale and self._sm.state == State.DRIVING):
            self._db.save_position(self._vehicle_id, data)

        events = self._sm.update(data)
        for event in events:
            self._handle_event(event, data)

        # During active trip: record GPS point and accumulate regen.
        # Regen = energy flowing INTO the pack while unplugged. charge_power_kw is now a
        # magnitude (|current×voltage|), so we gate on a clearly-negative charge current
        # (1178 < 0 = into pack, per the Leapmotor convention). The B10 sign still needs
        # on-road verification — gating this way stays conservative: at worst it counts 0,
        # never mistaking driving discharge for regen.
        if self._sm.state == State.DRIVING and self._active_trip_id and not stale:
            self._db.add_trip_position(self._active_trip_id, data)
            if not data.plug_connected and data.charge_current_a < -3.0:
                self._regen_kwh += data.charge_power_kw * (self._sm.poll_driving / 3600)

        # During active charge: track peak power, and sum the wallbox counter's rises so the billed
        # energy is MEASURED (reset/race-proof). Both are persisted → survive a poller restart mid-charge.
        if self._sm.state == State.CHARGING and self._active_charge_id:
            if data.charge_power_kw > self._max_charge_kw:
                self._max_charge_kw = data.charge_power_kw
                self._db.update_charge_max_power(self._active_charge_id, self._max_charge_kw)
            wb = self._read_wallbox_energy()
            if wb is not None:
                self._db.accumulate_wallbox_energy(self._active_charge_id, wb)
                log.debug("Charge #%d: wallbox counter %.3f kWh", self._active_charge_id, wb)

        # Order matters: trip reconstruction reads the SoC baseline (for the energy delta) BEFORE the
        # charge reconstruction advances it. Trip advances its OWN odometer baseline.
        self._maybe_reconstruct_trip(data)
        self._maybe_reconstruct_charge(data)

    def _maybe_reconstruct_trip(self, data: VehicleData) -> None:
        """Catch a DRIVE that was never seen live — the trip twin of _maybe_reconstruct_charge (#118).
        While the car is offline to the cloud the poller gets no live signals (or only stale ones), so a
        whole trip can happen without a single DRIVING poll: the live state machine never opens a trip and
        it's lost (same root as the missed-charge case #29). The one trace left is the ODOMETER that jumped
        while the car looks parked. Detect that jump and reconstruct the trip from the odometer delta.

        Runs every poll; the odometer baseline advances each poll, so a LIVE trip (odometer rising while
        state == DRIVING) is skipped here — the live path records those, with GPS. We only reconstruct when
        parked, with no trip open, the odometer clearly advanced (≥1 km, both readings valid — the 0-glitch
        guard), and the SoC did NOT rise (a rise means a charge, which _maybe_reconstruct_charge owns)."""
        prev_odo, prev_soc, prev_ts = self._last_odometer, self._last_soc, self._last_soc_ts
        self._last_odometer = data.odometer_km                  # advance the odometer baseline every poll
        if prev_odo is None or prev_soc is None or prev_ts is None:
            return
        if self._sm.state not in _PARKED_STATES or self._active_trip_id is not None:
            return                                              # a live trip owns this drive
        if not (prev_odo > 0 and (data.odometer_km or 0) > prev_odo):
            return                                              # no advance / 0-glitch reading → skip
        if (data.odometer_km - prev_odo) < self._reconstruct_min_km:
            return                                              # sub-1 km blip, not a trip
        if data.soc - prev_soc > 0.5:
            return                                              # SoC rose → a charge, not a pure drive
        self._db.create_reconstructed_trip(self._vehicle_id, prev_soc, prev_odo, prev_ts, data)

    def _maybe_reconstruct_charge(self, data: VehicleData) -> None:
        """Catch a charge that was never seen live. While the car is asleep/offline to the cloud
        the poller gets no live signals (EmptyStatusError) — or only stale ones — so a home charge
        can start and finish without a single poll ever showing plug/current: the live state machine
        never enters CHARGING and the session is lost (GitHub #29; same root as the "not real-time"
        reports #27/#28). The one trace left is a SoC that JUMPED up while parked. Detect that jump
        and reconstruct the charge from the SoC delta.

        Runs every poll. The baseline advances each poll, so a live charge (whose SoC rises gradually
        while state == CHARGING) is skipped here — the live path records those, with real power and
        wallbox cost. We only reconstruct when parked, with no charge open, and the rise clears the
        threshold (so vampire-drain drops and BMS recalibration jitter never invent a phantom charge)."""
        prev_soc, prev_ts = self._last_soc, self._last_soc_ts
        self._last_soc, self._last_soc_ts = data.soc, _now_iso()   # advance baseline every poll
        if prev_soc is None or prev_ts is None:
            return
        if self._sm.state not in _PARKED_STATES or self._active_charge_id is not None:
            return                                                 # live charge/trip owns this
        if data.soc - prev_soc < self._reconstruct_min_pct:
            return                                                 # drop or jitter, not a charge
        self._db.create_reconstructed_charge(self._vehicle_id, prev_soc, prev_ts, data)

    # HA's leapmotor_trip ignores movements shorter than 0.5 km ("spostamento breve
    # ignorato"). Match it: finalize the trip, then drop it if it was a short hop.
    _MIN_TRIP_KM = 0.5

    def _finalize_trip(self, data: VehicleData) -> None:
        distance_km = self._db.finalize_trip(self._active_trip_id, data, self._regen_kwh)
        if distance_km is not None and distance_km < self._MIN_TRIP_KM:
            self._db.delete_trip(self._active_trip_id)
            log.info("Trip #%d discarded — short hop %.2f km (< %.1f km)",
                     self._active_trip_id, distance_km, self._MIN_TRIP_KM)

    def mark_offline(self) -> None:
        events = self._sm.mark_offline()
        for e in events:
            self._handle_event(e, None)

    def mark_online(self) -> None:
        events = self._sm.mark_online()
        for e in events:
            self._handle_event(e, None)

    def _read_wallbox_energy(self) -> Optional[float]:
        """Current wallbox kWh-counter reading from Home Assistant (best-effort, never raises).
        Returns None when no wallbox is configured/reachable → the charge falls back to DC billing.
        Reuses web/ha_client.get_live() (the same reader the web layer uses)."""
        try:
            import sys
            import pathlib
            web = str(pathlib.Path(__file__).resolve().parent.parent / "web")
            if web not in sys.path:
                sys.path.insert(0, web)
            import ha_client
            return ha_client.get_live().get("energy_kwh")
        except Exception as e:  # noqa: BLE001
            log.debug("wallbox energy read failed: %s", e)
            return None

    def _handle_event(self, event: StateEvent, data: Optional[VehicleData]) -> None:
        frm, to = event.from_state, event.to_state

        if to == State.DRIVING:
            self._regen_kwh = 0.0
            self._active_trip_id = self._db.create_trip(self._vehicle_id, data)

        elif frm == State.DRIVING and to in _PARKED_STATES:
            if self._active_trip_id and data:
                self._finalize_trip(data)
            self._active_trip_id = None
            self._regen_kwh = 0.0

        elif to == State.CHARGING:
            if self._active_trip_id and data:
                # Plug inserted while driving → trip closed immediately, no 20s wait
                self._finalize_trip(data)
                self._active_trip_id = None
                self._regen_kwh = 0.0
            # Only OPEN a new charge if none is already open. Re-entering CHARGING with a
            # charge still open means we never unplugged — typically an OFFLINE gap mid-charge
            # (3 API errors → OFFLINE → recovery → CHARGING). Opening a second row there would
            # fragment one plug-in into two OVERLAPPING charges, whose power windows and costs
            # then bleed into each other (GitHub #23). Resume the open charge instead.
            if self._active_charge_id is None:
                self._max_charge_kw = 0.0
                if data:
                    self._active_charge_id = self._db.create_charge(self._vehicle_id, data)
                    start_wb = self._read_wallbox_energy()      # seed the wallbox-counter baseline
                    if start_wb is not None:
                        self._db.set_charge_wallbox_start(self._active_charge_id, start_wb)
                        log.info("Charge #%d: wallbox counter at start = %.3f kWh",
                                 self._active_charge_id, start_wb)

        elif frm == State.CHARGING and to in _PARKED_STATES:
            if self._active_charge_id and data:
                end_wb = self._read_wallbox_energy()              # final reading → capture the last rise
                if end_wb is not None:
                    self._db.accumulate_wallbox_energy(self._active_charge_id, end_wb)
                    log.info("Charge #%d: wallbox counter at stop = %.3f kWh",
                             self._active_charge_id, end_wb)
                self._db.finalize_charge(
                    self._active_charge_id, data, max_power_kw=self._max_charge_kw,
                )
            self._active_charge_id = None
            self._max_charge_kw = 0.0
