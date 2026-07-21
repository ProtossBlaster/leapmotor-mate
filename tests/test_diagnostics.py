"""The diagnostics bundle is meant to be safe to paste into a public GitHub issue, so the
redaction is a privacy GUARANTEE — these lock it against regressions. Adversarial inputs go
well beyond what Mate actually logs (it only ever logs the VIN); we scrub them anyway."""
import diagnostics as D


def test_mask_vin():
    assert D.mask_vin("LFZA5AE24SE008234") == "LFZ…8234"
    assert D.mask_vin(None) == "—"
    assert D.mask_vin("short") == "…"


def test_redact_vin_even_inside_a_log_line():
    out = D._redact("2026-06-09 [INFO] poller: Polling VIN WBADT43452G296000 model B10")
    assert "WBADT43452G296000" not in out
    assert "WBA…6000" in out


def test_redact_credentials():
    out = D._redact("auth password=hunter2 token=ghp_FAKE1234567890 pin=4321 secret=abcd api_key=zzz")
    for leak in ("hunter2", "ghp_FAKE1234567890", "4321", "abcd", "zzz"):
        assert leak not in out, leak
    assert out.count("***") >= 5


def test_redact_authorization_header_and_bearer():
    # The token AFTER "Authorization: Bearer" must not survive (the gap the live test caught).
    out = D._redact("login failed Authorization: Bearer abc.def.ghi trailing")
    assert "abc.def.ghi" not in out
    # A standalone bearer token too.
    out2 = D._redact("sent Bearer xyz.123.tok here")
    assert "xyz.123.tok" not in out2


def test_redact_email():
    out = D._redact("login user=test@example.com failed")
    assert "test@example.com" not in out
    assert "***@***" in out


def test_redact_json_style_credentials():
    # The gap the adversarial review caught: a JSON/config dump in a log line.
    for blob, leak in [
        ('{"password": "secret123"}', "secret123"),
        ("{'token': 'abc123xyz'}", "abc123xyz"),
        ('config secret: "hunter2"', "hunter2"),
    ]:
        out = D._redact(blob)
        assert leak not in out, blob


def test_redact_compound_key_names():
    # private_key / access_token etc. — underscore used to defeat the \bkey\b boundary.
    for blob, leak in [
        ("private_key=AKIA_DEADBEEF", "AKIA_DEADBEEF"),
        ("access_token: tok_9988", "tok_9988"),
        ("api_key=zzz999", "zzz999"),
    ]:
        out = D._redact(blob)
        assert leak not in out, blob


def test_redact_quoted_value_with_spaces():
    out = D._redact('pass="my long secret phrase" done')
    assert "my long secret phrase" not in out
    assert "done" in out          # we stop at the closing quote, not the whole line


def test_redact_device_id_and_masked_email():
    # Real leak Silvio's own download exposed: the API auth line logs device_id in the clear,
    # and the email keeps its domain (the app pre-masks only the local part).
    line = ("authenticating as account: sil***@dxc.com | "
            "device_id: ee059adfc09342859867423cccf53afc")
    out = D._redact(line)
    assert "ee059adfc09342859867423cccf53afc" not in out
    assert "dxc.com" not in out
    assert "***@***" in out


def test_redact_does_not_overmask_innocent_words():
    # "monkey"/"passenger" must survive — they only *contain* a secret word, not equal one.
    line = "the monkey passed the passenger a key-shaped cookie"
    assert D._redact(line) == line


def test_redact_keeps_normal_lines_intact():
    line = "2026-06-09 [INFO] leapmotor_mate: SOC 92.3% | Range 400 km | State: parked_active"
    assert D._redact(line) == line


def test_redact_camelcase_operate_password():
    # riri19 #1: the Leapmotor remote-control field operatePassword (camelCase, no separator) was
    # leaking — the compound-key regex requires a _/- separator, so camelCase slipped through.
    assert "123456" not in D._redact('control req {"operatePassword": "123456"} sent')
    assert "hunter2" not in D._redact("operatePassword=hunter2 next")
    # innocent camelCase ending in a non-secret word must survive untouched
    assert D._redact("compassHeading: 270 passengerCount: 2") == "compassHeading: 270 passengerCount: 2"


def test_redact_lowercase_vin_in_mqtt_topic():
    # riri19 #2: the VIN appears lowercase and glued inside the HA MQTT discovery topic, which the
    # generic uppercase \b regex can't see. With the known VIN passed in, it's masked any case.
    vin = "LFZA5AE2XSE000820"
    out = D._redact("MQTT: homeassistant/sensor/leapmotor_mate_lfza5ae2xse000820/soc/config", vin)
    assert "lfza5ae2xse000820" not in out.lower()
    assert "LFZ…0820" in out


def test_redact_gps_coords_truncated():
    # riri19 #3: the trip-start log carries exact coords; truncate the paren pair to ~1 decimal.
    out = D._redact("Trip #15 started — SOC 27.4% @ (45.4717, 1.5433)")
    assert "45.4717" not in out and "1.5433" not in out
    assert "(45.4…, 1.5…)" in out
    assert "27.4%" in out                     # a non-coord number is NOT touched


# ── the bundle must reproduce the battery page, not a default recomputation ──────
def test_vampire_section_honours_the_users_min_hours_setting(monkeypatch):
    """#154: the section passed only `min_drop_pct`, so it silently recomputed with the DEFAULT
    1 h while the battery page uses the user's `vampire_min_hours`. A user who had raised that
    threshold got a bundle listing short parks their own chart had filtered out — 34 windows in
    the file vs 21 on screen — which sent triage down a false trail. Both tunables must be
    forwarded, or the bundle contradicts the very screen it exists to explain."""
    import sqlite3
    from datetime import datetime, timedelta, timezone as _tz
    import db_reader

    monkeypatch.setattr(db_reader, "_local_tz", lambda: _tz.utc)
    base = datetime.now(_tz.utc).replace(microsecond=0) - timedelta(days=3)

    def P(offset_h, soc, speed=0, odo=1000.0, ready=0):
        return ((base + timedelta(hours=offset_h)).isoformat(), soc, 0, speed, odo, 0, ready)

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE positions (recorded_at TEXT, soc REAL, charging INT, "
                "speed_kmh REAL, odometer_km REAL, ac_port_mode INT, ready INT)")
    con.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?,?)", [
        P(0, 80.0), P(2, 79.5),                     # short park: 2 h, −0.5 %
        P(2.5, 79.2, speed=40, odo=1010, ready=1),  # a drive splits the two parks
        P(4, 79.0, odo=1010), P(24, 78.0, odo=1010),  # long park: 20 h, −1.0 %
    ])
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)

    settings = {"vampire_min_drop_pct": "0.2", "vampire_min_hours": "10"}
    monkeypatch.setattr(db_reader, "get_setting", lambda k, d=None: settings.get(k, d))

    # With the user's 10 h threshold only the long park is charted — exactly what their page shows.
    assert "count=1" in D._vampire_section()

    # Sanity: the same data at the default 1 h charts both, so the assertion above is really
    # testing that the setting was forwarded (not that the fixture only ever yields one window).
    settings["vampire_min_hours"] = "1"
    assert "count=2" in D._vampire_section()
