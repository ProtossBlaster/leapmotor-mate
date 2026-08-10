"""
Recorder: reacts to state machine events to persist trips, charges, and positions.
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from db import Database, _WB_STUCK_MIN_KW, _now_iso
from state_machine import State, StateMachine, StateEvent, _PARKED_STATES
from client import VehicleData

log = logging.getLogger(__name__)


def _frame_iso(data: VehicleData) -> Optional[str]:
    """The CAR's own clock on this frame, as ISO-UTC. None when the frame carries no timestamp."""
    if not getattr(data, "timestamp_ms", None):
        return None
    return datetime.fromtimestamp(data.timestamp_ms / 1000, timezone.utc).isoformat()


class Recorder:
    def __init__(self, db: Database, vehicle_id: int):
        self._db = db
        self._vehicle_id = vehicle_id
        self._sm = StateMachine()
        self._active_trip_id: Optional[int] = None
        self._active_charge_id: Optional[int] = None
        self._regen_kwh: float = 0.0
        self._max_charge_kw: float = 0.0
        # Whether the active charge may trust the home wallbox counter (decided from its GPS at open
        # / resume). Default True = attribute, so anything unlocated behaves exactly as before.
        self._charge_at_wallbox: bool = True
        self._started: bool = False
        # SoC-jump charge reconstruction (GitHub #29): baseline SoC + when we last saw it.
        self._last_soc: Optional[float] = None
        self._last_soc_ts: Optional[str] = None
        self._reconstruct_min_pct: float = 2.0   # min SoC rise to call it a (missed) charge
        # Odometer-jump TRIP reconstruction (#118): baseline odometer. A parked car's odometer never
        # moves, so any jump while parked = a drive we missed offline. Whole-km signal → 1 km floor.
        self._last_odometer: Optional[float] = None
        self._reconstruct_min_km: float = 1.0
        # When the cloud last told us something NEW — our own clock, stamped only on a fresh frame.
        # Deliberately NOT the SoC baseline above, which moves on every poll: while the link is dark
        # the car still looks online, so that one collapses a whole drive into one polling interval
        # (#244). None until the first frame arrives, and on a car that sends no frame clock it stays
        # None for ever → the reconstruction falls back to the old baseline, unchanged.
        self._last_fresh_ts: Optional[str] = None
        # Where the car was on the poll before this one (#233). Only ever read by _offline_head, to
        # anchor a trip that opened late to the place it actually left from instead of the point,
        # kilometres down the road, where the cloud finally started talking again.
        self._last_lat: Optional[float] = None
        self._last_lon: Optional[float] = None
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
        # Same guard as the state machine: a charge postponed to its programmed window (1149 == 4)
        # reads as plugged, and must not RESUME a session left open by the previous run — the car
        # is holding the cable, not charging (#243).
        is_charging = data.charging_status > 0 or (data.plug_connected and not data.charge_deferred)
        is_driving  = data.gear in ("D", "R", "N") or data.speed_kmh > 1

        open_charge = self._db.get_open_charge(self._vehicle_id)
        if open_charge:
            if is_charging:
                self._active_charge_id = open_charge["id"]
                self._max_charge_kw = open_charge["max_power_kw"] or 0.0
                self._charge_at_wallbox = self._db.wallbox_energy_applies(
                    self._vehicle_id, open_charge["latitude"], open_charge["longitude"])
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
            # And the frame baseline, for the same reason the other two are seeded: it lives in memory,
            # so without this the FIRST poll after any restart can never be a repeat — and a frame the
            # cloud is only re-serving gets recorded as if it were fresh. See get_last_frame_ts.
            self._last_frame_ts = self._db.get_last_frame_ts(self._vehicle_id)

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
        # The moment BEFORE this poll that the cloud last had news. Read now, advanced at the end of
        # the cycle, so the reconstruction below still sees where the silence began rather than where
        # it ended. A car with no frame clock never sets it → `None` → old behaviour.
        fresh_ts_before = self._last_fresh_ts

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
            if self._charge_at_wallbox:
                wb = self._read_wallbox_energy()
                if wb is not None:
                    # What the CAR says it took over this poll — the only thing that can tell a
                    # STOPPED counter from a slow one (#215). Sent only while the car reports real
                    # power: below that a flat counter proves nothing, because nothing is flowing.
                    car_kwh = (data.charge_power_kw * (self._sm.poll_interval / 3600)
                               if data.charge_power_kw >= _WB_STUCK_MIN_KW else 0.0)
                    self._db.accumulate_wallbox_energy(self._active_charge_id, wb, car_kwh)
                    log.debug("Charge #%d: wallbox counter %.3f kWh", self._active_charge_id, wb)

        # Order matters: trip reconstruction reads the SoC baseline (for the energy delta) BEFORE the
        # charge reconstruction advances it. Trip advances its OWN odometer baseline.
        self._maybe_reconstruct_trip(data, fresh_ts_before)
        self._maybe_reconstruct_charge(data)
        # Last, so everything above still saw the previous one. No `and data.timestamp_ms` guard:
        # a frame with no clock is never `stale`, so it advances anyway — and it should. Not
        # advancing on it would freeze the baseline on a car that reports fine but timestamps only
        # some frames, stretching a window that was never dark. (Written with the guard first; a
        # mutation survived because the two branches are identical, which is what proved it dead.)
        if not stale:
            self._last_fresh_ts = _now_iso()

    def _offline_head(self, data: Optional[VehicleData]):
        """The odometer and SoC of the poll BEFORE this trip opened, when they show the car already
        drove while we couldn't see it — the kilometres @riri19 lost off the front of a trip (#130).

        A trip opens on the first FRESH frame. While the car is out of touch the cloud re-serves the
        last frame it holds — gear P, speed 0 — so the state machine stays parked through the
        opening kilometres and the trip is then created with the odometer read AFTER them.
        _maybe_reconstruct_trip cannot rescue those either: it advances its own baseline and only
        then bows out to the live trip, so the jump is consumed and discarded in that same poll.

        The previous reading is still in memory right here — both baselines are advanced later in
        the same process() cycle — so hand it to create_trip as the trip's start anchor. A parked
        car's odometer never moves, so any advance at all is a drive; the floor is the same
        whole-kilometre resolution _maybe_reconstruct_trip uses, because below it the signal cannot
        tell a drive from its own quantisation.

        DISTANCE, ENERGY and the start POSITION. started_at is still not moved (the frozen window
        may hold hours of parking, so when the car set off is unknown — duration stays as observed,
        which does make the average speed of such a trip read high).

        ⚠️ The position was excluded until v3.8.8 on the grounds that "a frozen frame's GPS is
        routinely 0,0", which would plant the trip in the Gulf of Guinea. That risk is real but it
        is not a reason to throw the coordinate away — it is a reason to CHECK it, which is what
        _last_lat/_last_lon do by refusing to store a zero in the first place. When they hold a real
        fix, it is the place the car left from, and it beats the point kilometres down the road
        where the cloud started talking again: @riri19's 19 km drive (#233) opened 5 km from home,
        with the frame that said "parked at home" still 32 minutes stale in Mate's hand.

        Returns None in every ordinary case, which is all of them for a car whose cloud link is
        healthy.
        """
        prev_odo, prev_soc = self._last_odometer, self._last_soc
        if data is None or prev_odo is None or prev_soc is None:
            return None
        if not (prev_odo > 0 and (data.odometer_km or 0) > 0):
            return None                       # a 0 from a partial frame would hand us the lifetime
        gap_km = data.odometer_km - prev_odo
        if gap_km < self._reconstruct_min_km:
            return None
        head = {"odometer_km": prev_odo, "soc": prev_soc}
        if self._last_lat and self._last_lon:
            head["latitude"], head["longitude"] = self._last_lat, self._last_lon
        # NO coordinate in this line: the poller log ships inside the shareable bundle (#232).
        log.info("Trip opened %.1f km after it really started (car was out of touch) — anchoring "
                 "to the last seen odometer %.0f km / SoC %.1f%%%s", gap_km, prev_odo, prev_soc,
                 " and the place it left from" if "latitude" in head else
                 " (no usable position to anchor to)")
        return head

    def _maybe_reconstruct_trip(self, data: VehicleData,
                                fresh_ts_before: Optional[str] = None) -> None:
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
        # …and the position baseline with it. Zeros are not stored: (0,0) is the no-GPS marker, and
        # planting a trip's start there is the Gulf of Guinea trap the charge markers already hit.
        if data.latitude and data.longitude:
            self._last_lat, self._last_lon = data.latitude, data.longitude
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
        # Start where the news stopped, not where the last poll happened. The two are the same on a
        # healthy link and hours apart behind a frozen frame — and it is the second case that
        # produced 4 km "in 30 seconds", an implied 480 km/h, and a trip with no duration at all.
        started_at = fresh_ts_before or prev_ts
        trip_id = self._db.create_reconstructed_trip(self._vehicle_id, prev_soc, prev_odo,
                                                    started_at, data)
        if trip_id is not None:
            self._auto_note_trip(trip_id)

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
        charge_id = self._db.create_reconstructed_charge(self._vehicle_id, prev_soc, prev_ts, data)
        if charge_id is not None:
            self._auto_note_charge(charge_id)

    # HA's leapmotor_trip ignores movements shorter than 0.5 km ("spostamento breve
    # ignorato"). Match it: finalize the trip, then drop it if it was a short hop.
    _MIN_TRIP_KM = 0.5

    def _finalize_trip(self, data: VehicleData) -> None:
        distance_km = self._db.finalize_trip(self._active_trip_id, data, self._regen_kwh)
        if distance_km is not None and distance_km < self._MIN_TRIP_KM:
            self._db.delete_trip(self._active_trip_id)
            log.info("Trip #%d discarded — short hop %.2f km (< %.1f km)",
                     self._active_trip_id, distance_km, self._MIN_TRIP_KM)
            return
        self._auto_note_trip(self._active_trip_id)

    def mark_offline(self) -> None:
        events = self._sm.mark_offline()
        for e in events:
            self._handle_event(e, None)

    def mark_online(self) -> None:
        events = self._sm.mark_online()
        for e in events:
            self._handle_event(e, None)

    def _close_charge_driven_away(self, data: VehicleData) -> None:
        """Close a charge whose car turned up on the road — on the right reading, not this one.

        The live frame is no longer the end of the charge. @mikeeeeekoo's car finished at 100 %
        and read 98.1 % by the time Mate saw it: ten kilometres of road, not two points that never
        went in. So the end comes from the last reading taken WHILE CHARGING, dated by the car's
        own clock.

        ONE EXCEPTION, and it is a measurement rather than a guess: a car whose odometer has not
        moved cannot have spent anything, so if it reappears HIGHER than that last reading it kept
        charging while we were blind — Silvio's "lost at 80 %, seen at 95 %". When it HAS moved,
        the peak is unknowable (100 % then driven, or stopped at 97 %, look identical from here)
        and we keep the measured value rather than invent one.
        """
        end = self._db.charge_end_from_last_charging(self._active_charge_id)
        if end is not None:
            last_soc, _ended_at = end
            moved = (self._last_odometer or 0) > 0 and (data.odometer_km or 0) > 0 \
                and data.odometer_km > self._last_odometer
            if not moved and data.soc > last_soc:
                end = (data.soc, _frame_iso(data) or _now_iso())
        if self._charge_at_wallbox:
            end_wb = self._read_wallbox_energy()
            if end_wb is not None:
                self._db.accumulate_wallbox_energy(self._active_charge_id, end_wb)
        log.info("Charge #%d: the car is driving — closing it on the last reading seen while "
                 "charging (%s)", self._active_charge_id,
                 "SoC %.1f%% at %s" % end if end else "none available, using the live frame")
        self._db.finalize_charge(self._active_charge_id, data,
                                 max_power_kw=self._max_charge_kw, end_override=end)
        self._auto_note_charge(self._active_charge_id)
        self._active_charge_id = None
        self._max_charge_kw = 0.0

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

    @staticmethod
    def _web_db_reader():
        """web/db_reader.py — same sys.path trick as _read_wallbox_energy's ha_client
        import. Reused here since the auto-note generation (reverse-geocoding + station
        lookup) lives there, and pulls in only stdlib-http modules (web/geocode.py,
        web/charger_locator.py) already covered by web's own requirements — no new pip
        dependency for the poller."""
        import sys
        import pathlib
        web = str(pathlib.Path(__file__).resolve().parent.parent / "web")
        if web not in sys.path:
            sys.path.insert(0, web)
        import db_reader
        return db_reader

    def _auto_note_on(self) -> bool:
        """Whether the AUTOMATIC note may run. On by default — the feature is the point —
        but a trip's endpoints are, for most people, home and work, and this sends both to
        a reverse-geocoding service without being asked each time. Settings ▸ Geocoder can
        turn it off; the 🧭 button stays, so nobody loses the feature, they just decide
        when it happens. Read from the poller's own connection so an off switch costs no
        thread and no import."""
        try:
            return self._db.get_setting("auto_note", "1") != "0"
        except Exception:  # noqa: BLE001 — a settings read must never break recording
            return True

    def _auto_note_trip(self, trip_id: int) -> None:
        """Kick the address/time/temperature auto-note for a brand-new trip, off-thread —
        reverse-geocoding can take a few seconds and must never delay the next poll cycle.
        only_if_note_empty=True is the safety net: never clobbers a note the user
        somehow already typed in the few seconds between the trip closing and this
        thread running (the 🧭 button is the only thing allowed to overwrite a note, and
        only after the user confirms — see web/main.py trip_generate_auto_note)."""
        if not self._auto_note_on():
            return
        threading.Thread(target=self._auto_note_trip_body, args=(trip_id,), daemon=True).start()

    def _auto_note_trip_body(self, trip_id: int) -> None:
        try:
            db_reader = self._web_db_reader()
            provider = db_reader.get_setting("geocoder_provider", "")
            key = db_reader.get_secret("geocoder_key", "") or None
            db_reader.generate_trip_auto_note(trip_id, provider, key, only_if_note_empty=True)
        except Exception as e:  # noqa: BLE001 — best-effort, must never take the poller down
            log.debug("trip #%d auto-note failed: %s", trip_id, e)

    def _auto_note_charge(self, charge_id: int) -> None:
        """Same as _auto_note_trip, for a brand-new charge (station address + telemetry
        temperatures instead of reverse-geocoded endpoints + Open-Meteo)."""
        if not self._auto_note_on():
            return
        threading.Thread(target=self._auto_note_charge_body, args=(charge_id,), daemon=True).start()

    def _auto_note_charge_body(self, charge_id: int) -> None:
        try:
            db_reader = self._web_db_reader()
            provider = db_reader.get_setting("geocoder_provider", "")
            key = db_reader.get_secret("geocoder_key", "") or None
            db_reader.generate_charge_auto_note(charge_id, provider, key, only_if_note_empty=True)
        except Exception as e:  # noqa: BLE001 — best-effort, must never take the poller down
            log.debug("charge #%d auto-note failed: %s", charge_id, e)

    def _handle_event(self, event: StateEvent, data: Optional[VehicleData]) -> None:
        frm, to = event.from_state, event.to_state

        if to == State.DRIVING:
            # A car cannot be driving and charging. This is the mirror of the CHARGING branch
            # below, which closes an open trip on plug-in — and it was missing (#208,
            # @mikeeeeekoo): a charge is closed ONLY on CHARGING → parked, so a car that went
            # CHARGING → OFFLINE (three refused logins) → DRIVING left its charge open forever,
            # and an open charge appears in no calendar and in no AC count.
            if self._active_charge_id:
                self._close_charge_driven_away(data)
            self._regen_kwh = 0.0
            self._active_trip_id = self._db.create_trip(
                self._vehicle_id, data, head=self._offline_head(data))

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
                    # Only seed the wallbox baseline if this charge is AT the wallbox. A charge known
                    # to be far (public station) leaves the wallbox columns NULL → its idle/standby
                    # counter is never attributed, and it stays eligible for the 📍 station lookup.
                    self._charge_at_wallbox = self._db.wallbox_energy_applies(
                        self._vehicle_id, data.latitude, data.longitude)
                    if self._charge_at_wallbox:
                        start_wb = self._read_wallbox_energy()  # seed the wallbox-counter baseline
                        if start_wb is not None:
                            self._db.set_charge_wallbox_start(self._active_charge_id, start_wb)
                            log.info("Charge #%d: wallbox counter at start = %.3f kWh",
                                     self._active_charge_id, start_wb)
                    else:
                        log.info("Charge #%d: away from the home wallbox → its counter is not "
                                 "attributed to this charge", self._active_charge_id)

        elif frm == State.CHARGING and to in _PARKED_STATES:
            if self._active_charge_id and data:
                if self._charge_at_wallbox:
                    end_wb = self._read_wallbox_energy()          # final reading → capture the last rise
                    if end_wb is not None:
                        self._db.accumulate_wallbox_energy(self._active_charge_id, end_wb)
                        log.info("Charge #%d: wallbox counter at stop = %.3f kWh",
                                 self._active_charge_id, end_wb)
                self._db.finalize_charge(
                    self._active_charge_id, data, max_power_kw=self._max_charge_kw,
                )
                self._auto_note_charge(self._active_charge_id)
            self._active_charge_id = None
            self._max_charge_kw = 0.0
