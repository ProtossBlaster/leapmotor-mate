"""A car whose frames go through the real parser and recorder, and the events derived from them.

Shared by the tests of `poller/events.py`. A frame is a raw signal map, as the cloud sends it, so
what the detector reads is exactly what `save_position` stores from such a frame.
"""
from datetime import datetime, timedelta, timezone

import client
import db as D
import recorder as R

VIN = "VINEVENTS00000001"
T0 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def signal(t, **over):
    """One cloud frame at car time `t`: parked, locked, alarm armed, nothing open, no cable, READY
    off. A signal set to None is left out of the frame, as a car that does not send it would."""
    sig = {"1": int(t.timestamp() * 1000), "100003": 80.0, "1010": 0, "1319": 0, "1318": 12345,
           "3": 45.0, "2": 9.0, "1298": 1, "1255": 2, "1258": 0, "1149": 0, "47": 0}
    for key, value in over.items():
        if value is None:
            sig.pop(key, None)
        else:
            sig[key] = value
    return sig


class Car:
    """The poller's side of one car: its database, its recorder and the clock on its frames."""

    def __init__(self, tmp_path, name="events.db"):
        self.path = str(tmp_path / name)
        self.t = T0
        self.open()

    def open(self):
        self.db = D.Database(self.path)
        self.vid = self.db.ensure_vehicle(VIN, "B10")
        self.rec = R.Recorder(self.db, vehicle_id=self.vid)

    def restart(self):
        """A new poller process: everything it knows, it reads back from the database."""
        self.db.close()
        self.open()

    def frame(self, seconds=30, **over):
        """The next frame, `seconds` of car time after the previous one; returns the stored row."""
        self.t += timedelta(seconds=seconds)
        return self.repeat(**over)

    def repeat(self, **over):
        """The cloud serving the last frame again: the same car clock, stored as the poller does."""
        self.rec.process(client._parse_signal(VIN, signal(self.t, **over)))
        return self.db._conn.execute("SELECT * FROM positions ORDER BY id DESC LIMIT 1").fetchone()

    def derive(self, max_rows=5000) -> int:
        return self.db.derive_events(self.vid, max_rows)

    def events(self):
        return [(r["kind"], r["state"], r["at"]) for r in self.db._conn.execute(
            "SELECT kind, state, at FROM events ORDER BY id")]
