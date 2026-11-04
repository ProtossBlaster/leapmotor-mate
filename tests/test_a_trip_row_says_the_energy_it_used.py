"""A trip's row on Trips says the energy the trip used, under its distance and over its consumption.

It is the figure the day's heading adds up (db_reader.trip_energy_used), rounded as the heading rounds its
total, so a trip alone in its day reads the day's figure. A range-extender's row has none, as its totals
have none.
"""
import re

from test_a_trip_shows_where_it_started_and_ended import HOME, WORK, car, drawer, trip  # noqa: F401  (car: fixture)


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def driven(db, km=5.0, efficiency=18.0):
    tid = trip(db, HOME, WORK)
    db._conn.execute("UPDATE trips SET distance_km = ?, efficiency_kwh_100km = ? WHERE id = ?", (km, efficiency, tid))
    db._conn.commit()


def heading_and_row(db):
    heading, row = re.split(r'<a href="trips/\d+"', drawer(db), maxsplit=1)
    return text(heading), text(row.split("</a>")[0])


def test_a_row_says_the_energy_under_its_distance(car):
    driven(car)
    assert "5 km 0.9 kWh 18 kWh/100km" in heading_and_row(car)[1]


def test_a_trip_alone_in_its_day_reads_the_days_figure(car):
    driven(car, km=12.5, efficiency=17.0)                                 # 2.125 kWh, a tie of the hundredth
    heading, row = heading_and_row(car)
    assert "2.13 kWh" in heading and "12.5 km 2.13 kWh" in row


def test_a_range_extenders_row_has_no_energy(car):
    car.set_setting("is_reev", "1")
    driven(car)
    assert "5 km 18 kWh/100km" in heading_and_row(car)[1]                # nothing between the distance and the pill
