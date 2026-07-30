"""
Adaptive polling state machine.

States and intervals:
  PARKED_SLEEP   5 min   — no activity for 30+ min (car sleeping)
  PARKED_ACTIVE  60s     — normal parked, nothing unusual
  PARKED_ALERT   15s     — something changed (door/lock/temp): drive imminent
  DRIVING        10s     — speed > 0 or gear D
  CHARGING       60s     — plugged in
  OFFLINE        parked  — cloud unreachable; keeps the parked cadence (re-login rate-limited 60s)

Transitions (all independent of HA and phone):
  UNKNOWN/OFFLINE     → PARKED_ACTIVE  first successful poll
  PARKED_SLEEP        → PARKED_ACTIVE  fingerprint changes (any signal)
  PARKED_ACTIVE       → PARKED_ALERT   fingerprint changes
  PARKED_ACTIVE       → PARKED_SLEEP   no change for SLEEP_AFTER_S (30 min)
  PARKED_ALERT        → DRIVING        speed > 0 or gear D
  PARKED_ALERT        → PARKED_ACTIVE  no drive within ALERT_EXPIRES_S (5 min)
  DRIVING             → PARKED_ACTIVE  gear P held ~1 min (6 × 10s), OR cable plugged (trip ends now)
  ANY_PARKED          → CHARGING       charging_status > 0  (REAL current / 1149==2; NOT the cable alone)
  CHARGING            → PARKED_ACTIVE  no current AND the cable reads gone (1149→0) — a dip with the
                                       cable still connected won't close. NB: a modulating wallbox
                                       makes the car report the cable gone too (see is_charging)
  ANY                 → OFFLINE        3 consecutive API errors
"""
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from client import VehicleData

log = logging.getLogger(__name__)

SLEEP_AFTER_S   = 1800   # 30 min without changes → PARKED_SLEEP
ALERT_EXPIRES_S = 300    # 5 min in PARKED_ALERT without driving → back to ACTIVE

# ── REEV-only charge detection ────────────────────────────────────────────────
# A range-extender charging in AC at home reports NEITHER of the two things the normal detector
# looks for: the cable state sits at 1 ("connected") instead of 2 ("charging"), and the pack
# current reads ~0.1 A instead of a real charge current. Verified against michapr's B10 (beta
# #12): across 15 days the poller never once entered CHARGING, while the SoC visibly climbed —
# 36.4 → 36.8 % in three minutes with the state stuck on "parked".
#
# So for REEVs only, take the rise itself as the evidence: cable connected, car stationary, and
# the battery genuinely climbing between polls. The SoC-rise requirement is what separates a real
# charge from a SCHEDULED one waiting for its slot (cable connected, remaining-time present, but
# the battery flat) — the case the old comment warns must never open a session.
#
# BEVs are deliberately untouched: they report cable/current correctly and are working today.
#
# Measured against the LOWEST SoC seen since the cable went in, not against the previous poll:
# his battery climbs one 0.1 % step at a time, which is also the SoC's own read resolution, so a
# poll-to-poll test either misses a real charge (threshold above the step) or fires on noise
# (threshold at the step). Accumulating from the low-water mark separates them cleanly — a charge
# keeps adding up, a wobble doesn't.
_REEV_SOC_RISE_PCT = 0.3   # total climb from the low-water mark that means "this is charging"
# End a trip only after the car has been in gear P for ~1 min — matches the HA
# leapmotor_trip automation (gear → P, for: minutes: 1). At the 10s driving poll
# that's 6 readings. Gear-based (not speed) so red lights / brief stops, where the
# gear stays D, never split one drive into many trips.
PARKED_CONFIRM  = 6      # consecutive gear-P readings to end a trip (~1 min @ 10s)


class State(Enum):
    UNKNOWN       = "unknown"
    PARKED_SLEEP  = "parked_sleep"
    PARKED_ACTIVE = "parked_active"
    PARKED_ALERT  = "parked_alert"
    DRIVING       = "driving"
    CHARGING      = "charging"
    OFFLINE       = "offline"


# Default poll cadence (seconds). Polling the Leapmotor cloud does NOT wake/drain the
# car (it reads the last cloud-reported state), so a steady ~30s parked cadence is safe
# and keeps Mate independent (no HA/boost needed to catch a trip start). User-tunable.
DEFAULT_POLL_PARKED  = 30
DEFAULT_POLL_DRIVING = 10

_PARKED_STATES = {State.PARKED_SLEEP, State.PARKED_ACTIVE, State.PARKED_ALERT}


@dataclass
class StateEvent:
    from_state: State
    to_state: State
    data: Optional[VehicleData]


@dataclass
class StateMachine:
    state: State = State.UNKNOWN
    poll_parked: int           = DEFAULT_POLL_PARKED    # tunable from Settings
    poll_driving: int          = DEFAULT_POLL_DRIVING
    _prev_fp: Optional[tuple]  = field(default=None,  repr=False)
    _last_change_ts: float     = field(default=0.0,   repr=False)
    _alert_start_ts: float     = field(default=0.0,   repr=False)
    _parked_count: int         = field(default=0,     repr=False)
    _error_count: int          = field(default=0,     repr=False)
    _v2l_active: bool          = field(default=False, repr=False)
    _reev_last_soc: Optional[float] = field(default=None, repr=False)   # REEV charge detection

    def update(self, data: VehicleData) -> list[StateEvent]:
        self._error_count = 0
        events: list[StateEvent] = []
        now = time.monotonic()

        # Trip is "active" while the car is in a driving gear (D/R/N) or moving.
        # Gear-based like HA: at a red light the gear stays D, so the trip is NOT
        # split — only a sustained gear P ends it (see DRIVING branch below).
        is_driving  = data.gear in ("D", "R", "N") or data.speed_kmh > 1
        # A charge SESSION opens only on REAL charging current: charging_status = signal 1149==2
        # ("charging") or a measured current ≥ 2 A. NEVER on the cable alone — plugging in with a
        # scheduled/deferred charge reports 1149==1 ("connected", no current) while the car waits for
        # the programmed time, and that must NOT start counting a charge (see _is_charging vs
        # _is_plugged_in). This is the documented ANY_PARKED→CHARGING condition (charging_status>0).
        charge_active = data.charging_status > 0
        # ...except on a REEV, where neither of those two ever appears during an AC home charge
        # (see _REEV_SOC_RISE_PCT above). There, the battery climbing while plugged in and parked
        # IS the charge. Strictly gated on is_reev — a BEV never reaches this branch.
        if getattr(data, "is_reev", False) and data.plug_connected and not is_driving:
            if self._reev_last_soc is None or data.soc < self._reev_last_soc:
                self._reev_last_soc = data.soc          # (re)set the low-water mark
            elif data.soc - self._reev_last_soc >= _REEV_SOC_RISE_PCT:
                if not charge_active:
                    log.info("REEV charge detected from SoC rise: %.1f → %.1f %% while plugged in",
                             self._reev_last_soc, data.soc)
                charge_active = True
        else:
            self._reev_last_soc = None      # cable out / driving → forget the reference
        # The session stays open across a current dip FOR AS LONG AS THE CABLE STILL READS
        # CONNECTED, and closes when both signals go.
        #
        # ⚠️ That is not the same as "closes only when you unplug", which is what this comment used
        # to claim. MEASURED on a B10, 29→30 July 2026 — cable in at 19:30, out the next morning,
        # never touched in between: when a load-balancing wallbox stops the current, the car itself
        # reports the cable GONE. plug_connected 1→0 and charging 1→0 in the same frame, current
        # −11.8 A → 0.5 A, remaining time → NULL, and everything back sixty seconds later. On FRESH
        # frames (frame_ts advancing every poll) — so it is neither the stale-frame artefact nor an
        # OFFLINE gap, both of which are handled elsewhere. That one plug-in was recorded as SIX
        # charges, with pauses of 60, 70, 180 and 60 s.
        #
        # So on such a wallbox this OR-term goes false on every pause and the session DOES fragment.
        # Deliberately not fixed here: a grace window would have to guess, and its guess would be
        # irreversible. The chosen remedy is to let the user join the rows afterwards, the way trips
        # are merged — reversible, and it never recomputes a cost.
        #
        # The cable also ends the trip immediately on plug-in (DRIVING branch), before any current flows.
        is_charging = charge_active or data.plug_connected
        # V2L (bidirectional discharge) is parked activity that changes with the load → poll fast.
        self._v2l_active = getattr(data, "ac_port_mode", 0) == 2
        fp          = data.fingerprint()
        fp_changed  = (self._prev_fp is not None) and (fp != self._prev_fp)

        if fp_changed:
            self._last_change_ts = now
        if self._prev_fp is None:
            self._last_change_ts = now
        self._prev_fp = fp

        # ── UNKNOWN / OFFLINE → first successful poll ─────────────────────
        if self.state in (State.UNKNOWN, State.OFFLINE):
            if is_driving:
                events.append(self._go(State.DRIVING, data))
            elif charge_active:
                events.append(self._go(State.CHARGING, data))
            else:
                events.append(self._go(State.PARKED_ACTIVE, data))
            return events

        # ── Any parked → CHARGING (real current only, not the cable alone) ─
        if self.state in _PARKED_STATES and charge_active:
            events.append(self._go(State.CHARGING, data))
            return events

        # ── Any parked → DRIVING ──────────────────────────────────────────
        if self.state in _PARKED_STATES and is_driving:
            self._parked_count = 0
            events.append(self._go(State.DRIVING, data))
            return events

        # ── PARKED_SLEEP ──────────────────────────────────────────────────
        if self.state == State.PARKED_SLEEP:
            if fp_changed:
                events.append(self._go(State.PARKED_ACTIVE, data))

        # ── PARKED_ACTIVE ─────────────────────────────────────────────────
        elif self.state == State.PARKED_ACTIVE:
            idle_s = now - self._last_change_ts
            if fp_changed:
                self._alert_start_ts = now
                events.append(self._go(State.PARKED_ALERT, data))
            elif idle_s >= SLEEP_AFTER_S:
                events.append(self._go(State.PARKED_SLEEP, data))

        # ── PARKED_ALERT ──────────────────────────────────────────────────
        elif self.state == State.PARKED_ALERT:
            alert_age_s = now - self._alert_start_ts
            if fp_changed:
                self._alert_start_ts = now  # reset timer on new activity
            elif alert_age_s >= ALERT_EXPIRES_S:
                events.append(self._go(State.PARKED_ACTIVE, data))

        # ── DRIVING ───────────────────────────────────────────────────────
        elif self.state == State.DRIVING:
            if charge_active:
                self._parked_count = 0
                events.append(self._go(State.CHARGING, data))
            elif data.plug_connected:
                # Cable inserted after the drive, but no current yet (e.g. a scheduled charge that
                # will start later): end the trip NOW — don't wait the ~1 min gear-P confirmation —
                # but do NOT open a charge. The charge opens later, from PARKED, when current flows.
                self._parked_count = 0
                self._alert_start_ts = now
                events.append(self._go(State.PARKED_ACTIVE, data))
            elif data.gear == "P":
                self._parked_count += 1
                if self._parked_count >= PARKED_CONFIRM:
                    self._parked_count = 0
                    self._alert_start_ts = now
                    events.append(self._go(State.PARKED_ACTIVE, data))
            else:
                self._parked_count = 0

        # ── CHARGING ──────────────────────────────────────────────────────
        elif self.state == State.CHARGING:
            if not is_charging:
                events.append(self._go(State.PARKED_ACTIVE, data))

        return events

    def mark_offline(self) -> list[StateEvent]:
        self._error_count += 1
        if self._error_count >= 3 and self.state != State.OFFLINE:
            return [self._go(State.OFFLINE, None)]
        return []

    def mark_online(self) -> list[StateEvent]:
        self._error_count = 0
        return []

    def _go(self, new_state: State, data) -> StateEvent:
        event = StateEvent(from_state=self.state, to_state=new_state, data=data)
        self.state = new_state
        log.info(
            "State: %-14s → %-14s  (poll: %ds)",
            event.from_state.value, new_state.value, self.poll_interval,
        )
        return event

    @property
    def poll_interval(self) -> int:
        if self._v2l_active:
            return self.poll_driving        # V2L discharge → poll fast (like a trip) to track power
        if self.state in (State.DRIVING, State.PARKED_ALERT):
            return self.poll_driving        # active / drive imminent → fast
        if self.state == State.UNKNOWN:
            return min(self.poll_parked, 30)
        # OFFLINE (cloud unreachable) keeps the user's parked cadence too — we never
        # hide-throttle to a long fixed backoff: a flaky car↔cloud link (or a sleeping car)
        # must be re-caught the moment it returns, so no trip start is lost. The re-login
        # attempt stays separately rate-limited to once/60s (see poller/main).
        return self.poll_parked             # OFFLINE / PARKED_SLEEP / PARKED_ACTIVE / CHARGING
