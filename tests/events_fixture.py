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


def web(car, monkeypatch, zone="Europe/Warsaw", lang="en"):
    """The web app on this car's database, as the browser reaches it: a Starlette TestClient."""
    import db_reader
    import main
    from starlette.testclient import TestClient
    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_reader, "DB_PATH", car.path)
    car.db.set_setting("setup_complete", "1")
    car.db.set_setting("timezone", zone)
    car.db.set_setting("language", lang)
    db_reader._lang_memo[0] = None            # the web remembers the language it read first
    return TestClient(main.app)


def grouped(lang="en", part=0, **query):
    """`get_events_grouped` for a query string's worth of filters, in `lang`."""
    import db_reader
    import i18n
    flt = db_reader.EventFilter.from_query(**query)
    return db_reader.get_events_grouped(flt, i18n.get_t(lang), lang, part=part)


def rows(ev):
    """Every row of every day, newest first."""
    return [e for day in ev["days"] for e in day["items"]]


def serve(client, ingress=""):
    """A Playwright route handler that answers mate.test from the TestClient, as Home Assistant's
    ingress would under `ingress`, and nothing else (no tiles, no CDN)."""
    def handle(route):
        req = route.request
        if not req.url.startswith("http://mate.test" + ingress + "/"):
            return route.fulfill(status=204)
        r = client.request(req.method, req.url.removeprefix("http://mate.test" + ingress),
                           headers={"x-ingress-path": ingress} if ingress else {})
        route.fulfill(status=r.status_code, body=r.content,
                      headers={k: v for k, v in r.headers.items() if k == "content-type" or k.startswith("hx-")})
    return handle


def event_row(car, kind, at, state=1, **cols):
    """One stored transition, as the poller writes it, at the UTC datetime `at`."""
    keys = ", ".join(cols)
    marks = "".join(", ?" for _ in cols)
    car.db._conn.execute(
        f"INSERT INTO events (vehicle_id, kind, at, state{', ' + keys if cols else ''}) VALUES (?, ?, ?, ?{marks})",
        (car.vid, kind, at.isoformat(), state, *cols.values()))
    car.db._conn.commit()


def row_text(html, anchor):
    """A row's words as the reader sees them: the parts after its icon, joined by the "·" the page
    draws between them (not before the label's state in brackets, nor before a chip)."""
    from html.parser import HTMLParser

    class Parts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth, self.row, self.txt, self.parts, self.glue = 0, None, None, None, []

        def handle_starttag(self, tag, attrs):
            self.depth += 1
            attrs = dict(attrs)
            if attrs.get("id") == anchor:
                self.row = self.depth
            elif self.row and self.parts is None and attrs.get("class") == "ev-txt":
                self.txt, self.parts = self.depth, []
            elif self.txt and self.depth == self.txt + 1:
                self.parts.append("")
                cls = attrs.get("class") or ""
                self.glue.append(" " if "ev-now" in cls or "ev-chip" in cls else " · ")

        def handle_endtag(self, tag):
            if self.depth == self.txt:
                self.txt = None
            self.depth -= 1

        def handle_data(self, data):
            if self.txt and self.depth > self.txt:
                self.parts[-1] += data

    p = Parts()
    p.feed(html)
    assert p.parts is not None, f"no row {anchor}"
    return "".join((p.glue[k] if k else "") + part.strip() for k, part in enumerate(p.parts))
