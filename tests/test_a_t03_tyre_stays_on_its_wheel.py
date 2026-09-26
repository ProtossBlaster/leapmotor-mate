"""A T03's tyre pressures must land on the wheels they are named after.

The T03 cloud sends the pressures as named fields — `leftFrontTirePressure` and so on — and the
adapter files them under numeric ids for the one parser both models share. The id↔name table was
copied from the library documentation, whose labels the parser's own comment calls wrong: checked
on two real B10s (#32), the ids run FL=2646, FR=2653, RL=2660, RR=2667, not the
documented LF=2667 / LR=2646. The adapter kept the documented pairing, so three of a T03's
pressures turned one wheel round: the left front was filed under the id the parser reads as REAR
RIGHT, the left rear under front left, the right rear under rear left — on the Vehicle page and in
Home Assistant. Only the right front survived. The alarm flags
(`...TirePressureState`) were already filed under the ids every public client agrees on and are
left alone. leapmotor-ha pairs a T03's named tyre fields with the same ids as this change.

The pressures below are made-up round numbers.
"""
import client
import command_client

_T03 = {"soc": 60, "gearStatus": 0, "speed": 0.0,
        "leftFrontTirePressure": 210, "rightFrontTirePressure": 220,
        "leftRearTirePressure": 230, "rightRearTirePressure": 240,
        "leftFrontTirePressureState": 1, "rightFrontTirePressureState": 0,
        "leftRearTirePressureState": 0, "rightRearTirePressureState": 0}


def test_the_poller_reads_each_pressure_on_its_own_wheel():
    vd = client._parse_signal("VIN", client._named_fields_to_signal(_T03))
    assert (vd.tire_fl_bar, vd.tire_fr_bar, vd.tire_rl_bar, vd.tire_rr_bar) == (2.1, 2.2, 2.3, 2.4)


def test_the_web_adapter_files_them_under_the_ids_the_parser_reads():
    """The Vehicle page reads 2646=FL / 2653=FR / 2660=RL / 2667=RR — the same pairing as the
    poller, from the web's own copy of the adapter. The alarms keep the ids leapmotor-api,
    leapmotor-ha and ioBroker all use (2641=FL, 2648=FR, 2655=RL, 2662=RR)."""
    sig = command_client._named_fields_to_signal(_T03)
    assert (sig["2646"], sig["2653"], sig["2660"], sig["2667"]) == (210, 220, 230, 240)
    assert (sig["2641"], sig["2648"], sig["2655"], sig["2662"]) == (1, 0, 0, 0)


def test_the_two_copies_of_the_adapter_agree():
    assert client._named_fields_to_signal(_T03) == command_client._named_fields_to_signal(_T03)
