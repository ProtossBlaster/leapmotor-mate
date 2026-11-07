"""How a frame reads: its signals, whether the cable is in and whether the car is charging.

Shared by the poller and the web, so it imports nothing: the web loads it from poller/ for the length
of one import, since the two directories share module names."""


def _sf(sig: dict, k: str):
    v = sig.get(k)
    try:    return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _si(sig: dict, k: str):
    v = sig.get(k)
    try:    return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def _charge_power_kw(sig: dict) -> float:
    """Charge/regen power from current (1178) × voltage (1177). Signal 49 is NOT a
    power — in the Leapmotor app it's the left-mirror-heating flag. Magnitude only;
    the recorder decides charge vs regen from the current sign.

    🔑 NO current floor here, and that is the point (#307, @arzthilfe). This used to refuse below
    `_CHARGE_CURRENT_MIN_A` — the user's CHARGE-DETECTION setting, 2.0 A by default — so his C10
    on a wallbox turned down to 8 A, tapering near 87 % SoC, reported 718.2 V × 1.599 A as
    **0.00 kW**. One threshold cannot both decide WHETHER the car is charging (where a floor
    belongs, and still lives — see `is_charging`) and measure HOW MUCH flows (where it prints a
    zero over a real figure). Zero current already gives zero power without a floor to say so.

    Nothing downstream loses a guard by this: regen has its own (`charge_current_a < -3.0`, in the
    recorder), the stuck-counter sum has `_WB_STUCK_MIN_KW`, and `max_power_kw` is a maximum, so a
    small reading can only lose to a bigger one."""
    current = _sf(sig, "1178")
    voltage = _sf(sig, "1177")
    if current is None or voltage is None:
        return 0.0
    return round(abs(current * voltage) / 1000.0, 3)


def is_plugged_in(sig: dict) -> bool:
    """Whether the charge cable is physically connected. Uses signal 1149 (charge
    state: 1–4 = a cable in, see below), gated by motion.

    Why 1149 and not 47: signal 47 (acInputSlowCharge) was the old primary, but on the
    B10 it LATCHES at 1 after an AC charge and only clears ~5 min later, when the car's
    charge controller tears down the AC subsystem — it does NOT drop on unplug. That kept
    a finished charge SESSION open long after the cable was pulled (the plug_connected
    OR-term in the state machine never went false), inflating the session window.
    Signal 1149 instead drops to 0 when the charge SESSION ends — on unplug, or once the car
    has wound the session down after reaching its limit (seconds on one B10, verified on-car
    twice with the session closing at once; minutes on another, see below), while 47 stayed
    latched at 1 for 10+ minutes (right through the physical unplug). Its only flaw is reading 1 spuriously
    during regen at speed, so we suppress it while the car is moving (it can never be
    plugged in while driving anyway — same motion gate as is_charging). Falls back to
    signal 47 only when 1149 is absent (other models).

    1 and 2 differ between cars. The official app interprets 1 as charging and 2 as finished, and a
    BEV B10 agrees: 1 for the whole of a charge, 2 from the moment it ends, then 0 a while later
    with the cable still in. A C10 charging AC (beta #13) and the B10 in #243 read 2 mid-charge.
    Either way the cable is in."""
    if _si(sig, "1010") not in (None, 0):       # gear R/N/D → moving, cannot be plugged
        return False
    if (_sf(sig, "1319") or 0) > 2.0:            # speed > 2 km/h → moving (gear may lag)
        return False
    conn = _si(sig, "1149")
    if conn is not None:
        # 1, 2 = connected (see above), 3=a third connected state the REEVs cycle THROUGH mid-charge
        # (seen on the C10/B10 range-extenders alongside 1=2, always parked, current ~0). Treating
        # 3 as unplugged made the cable read as pulled every time it flickered 2→3→2, which closed
        # and reopened the session and shredded one slow AC charge into many empty fragments — each
        # then dropped as a phantom (beta #12/#13). Keep the session whole across the flicker; the
        # motion gate above still bars anything that could read 3 while driving. (5 = the drive-time
        # cable code, deliberately NOT here.)
        # 4 = connected, but the charge is DEFERRED to the programmed window: the cable is in and
        # the car is deliberately not drawing. Measured on Silvio's B10 on 09/08/26 (#243) — he
        # enabled the schedule mid-charge and the code went 2 → 4 with the cable untouched, while
        # the official app, reading that same frame, kept drawing the cable. It used to fall into
        # "unplugged" by exclusion, which is what blanked the cable on the Overview. See
        # `_is_deferred_charge`: 4 must never be read as charging.
        return conn in (1, 2, 3, 4)
    return _si(sig, "47") == 1                   # legacy fallback when 1149 is missing


def is_charging(sig: dict, min_a: float) -> bool:
    """Whether the car is actually charging. Charging only happens while PARKED, so the
    car must be stationary (gear P, speed ~0); plus the cable plugged in (1149) AND a
    meaningful charge current (1178). The motion gate is essential: during regen braking
    the pack current is strongly negative (same sign as charging) AND 1149 reads 1
    spuriously, so without it driving is mistaken for charging — fragmenting trips and
    creating phantom charge sessions. Signal 1939 (AC fan mode) is NOT used."""
    if _si(sig, "1010") not in (None, 0):   # gear R/N/D → moving, cannot be charging
        return False
    if (_sf(sig, "1319") or 0) > 2.0:       # speed > 2 km/h → moving (gear signal may lag)
        return False
    # 5 is the drive-time cable code, NOT a connection — is_plugged_in leaves it out for the same
    # reason, and the two readers of 1149 must agree. The motion gate above does not always catch
    # it: on ebagnoli's C10 (beta #13) 1149 read 5 at 08:30 on 23/07 while the speed frame was
    # still the previous evening's 0 km/h, which opened a 0-minute session — harmless only because
    # it delivered nothing and the phantom guard dropped it.
    # ⚠️ 4 (charge postponed to the programmed window) is deliberately NOT excluded here, though it
    # is a "not drawing" code. We have measured 4 only at rest (0.1 A); nobody has watched a car
    # whose window OPENS while the code stays at 4. Excluding it would then drop the entire
    # scheduled charge, silently — the worse failure by far. Let the current decide, as it does for
    # every other code: parked with a real charge current IS a charge, whatever the cable says.
    if _si(sig, "1149") in (None, 0, 5):   # cable not connected → cannot be charging
        return False
    current   = _sf(sig, "1178")
    remaining = _si(sig, "1200")
    power     = _charge_power_kw(sig)

    if current is not None:
        if current >= min_a:       # + is current out of the pack: a load, not a charge
            return False
        if abs(current) < min_a:   # resting/plugged-idle → not charging
            # ...EXCEPT a C10 REEV charging in AC: on that car the pack current (1178) reads ~0
            # during slow AC charging — the on-board charger feeds the pack by a path this sensor
            # doesn't see — so the BEV rule "current ≥ 2 A" never fires and the session is missed
            # (beta #13 ebagnoli; same signature back to the earliest 1.36.1 bundle). Trust the
            # cable's OWN state when it reads 1149==2, as that car does while charging AC (a BEV
            # B10 sends 2 only once its charge has ended), AND a charge is in progress
            # (remaining minutes > 0). Driving is already gated out above, and a bare 0→2→0 cable
            # blip has no remaining-time so it still can't open a phantom.
            #
            # ⚠️ This is the C10 signature ONLY — NOT the B10 REEV, whatever this comment used to
            # say. @michapr (beta #12) was diagnosed as this case on 23/07 and it was wrong: his
            # pack current during AC charging reads −3.8 A, well above the floor. v2.8.4 shipped
            # this very rule for him and changed nothing, because his cable sits at 1149==1 and
            # only brushes 2 for a few seconds at a time — the test below essentially never fires
            # on that car. Retracted in the issue on 24/07 ("I had tuned it on a C10's signature
            # and wrongly assumed the two REEVs behaved the same"), and fixed for real in v2.8.6
            # by the SoC-rise branch in state_machine.py, which is what actually covers a B10 REEV.
            # Do not re-add michapr here: two REEV models, two different signatures, two rules.
            if _si(sig, "1149") == 2 and (_si(sig, "1200") or 0) > 0:
                return True
            return False
        return remaining is not None or power >= 1.0
    if power >= 1.0:
        return remaining is not None
    return _si(sig, "1149") == 2                    # fallback: 2 is "charging" on a C10 (see above)
