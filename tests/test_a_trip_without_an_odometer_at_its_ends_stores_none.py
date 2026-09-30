"""A trip that opens or closes on a reading without an odometer keeps no 0 as that end's odometer.

The parser reads an absent odometer (signal 1318) as 0 and says so in `odometer_reported`;
`positions` stores such a reading as NULL. The trip took the 0 from memory as its start or end
odometer, a kilometre count the car never gave, and the trips export and the diagnostics printed it.
The distance was not affected: it falls back to the route when either end has no odometer.
"""
import pytest
from test_unseen_kilometres_survive_a_reading_without_an_odometer import (  # noqa: F401  (fixtures)
    BLIND,
    KM,
    _gaps,
    _rebuilt,
    _rows,
    car,
    rig,
)

UNSENT = {"odo": BLIND, "odometer_reported": False}    # what the parser hands over without 1318


def _trip(db):
    trip, = _rows(db, "SELECT * FROM trips WHERE COALESCE(reconstructed, 0) = 0")
    assert trip["ended_at"] is not None
    return trip


def test_a_trip_opened_without_an_odometer_has_no_start_odometer(car):
    db, _rec, send = car
    send(0, odo=1000, soc=80.0)
    send(60, soc=80.0, gear="D", speed=30.0, **UNSENT)
    for km in range(1, 4):
        send(60, odo=1000 + km, soc=80.0 - km * 0.2, gear="D", speed=50.0, latitude=45.0 + km * KM)
    for _ in range(6):
        send(10, odo=1003, soc=79.4, latitude=45.0 + 3 * KM)

    trip = _trip(db)
    assert (trip["start_odometer_km"], trip["end_odometer_km"]) == (None, 1003)
    assert _rebuilt(db) == [] and _gaps(db) == []


@pytest.mark.parametrize("close", ["sixth P poll", "plug-in"])
def test_a_trip_closed_without_an_odometer_has_no_end_odometer(car, close):
    db, _rec, send = car
    for km in range(4):
        send(60 if km else 0, odo=1000 + km, soc=80.0 - km * 0.2, gear="D", speed=50.0,
             latitude=45.0 + km * KM)
    if close == "plug-in":
        send(10, soc=79.4, latitude=45.0 + 3 * KM, plug_connected=True, **UNSENT)
    else:
        for _ in range(6):
            send(10, soc=79.4, latitude=45.0 + 3 * KM, **UNSENT)

    trip = _trip(db)
    assert (trip["start_odometer_km"], trip["end_odometer_km"]) == (1000, None)
    assert trip["distance_km"] > 2.5, "the distance still comes from the route"
