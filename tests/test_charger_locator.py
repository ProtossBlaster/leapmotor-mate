"""📍 charging-station labels + Navigation nearby search (idea: @hubcasale, PR #48,
reimplemented web-side). The Overpass network seam (`_query` / `find_station_name`)
is always patched — no test ever touches the network or the settings DB; everything
runs on a tmp_path DB (poller schema + db_reader pointed at it), CI-safe."""
import pytest

import db as D            # poller schema (creates charges/settings tables + migrations)
import db_reader
import charger_locator as CL

# Real functions captured before the autouse fixture stubs them, for the tests that
# exercise the PUN source directly (the fixture replaces CL._pun_stations with a no-op).
_REAL_PUN = CL._pun_stations


@pytest.fixture(autouse=True)
def _no_extra_sources(monkeypatch):
    """OCM and PUN stay silent unless a test opts in — never read a real settings DB,
    hit the network, or query Italy from the default fixtures."""
    monkeypatch.setattr(CL, "_ocm_key", lambda: "")
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "")
    monkeypatch.setattr(CL, "_pun_stations", lambda *a, **k: [])


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(CL.time, "sleep", lambda *_: None)   # no etiquette pause in tests
    return pdb


def _charge(pdb, cid, *, lat=45.0, lon=9.0, ended="2026-06-02T21:18:36+00:00",
            ctype=None, ac=None, wb_start=None, name=None):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, latitude, longitude, location_type, ac_energy_kwh,"
        " wallbox_energy_start_kwh, location_name)"
        " VALUES (?,1,'2026-06-02T16:48:39+00:00',?,40,52,8.0,?,?,?,?,?,?)",
        (cid, ended, lat, lon, ctype, ac, wb_start, name))
    pdb._conn.commit()


def _row(pdb, cid):
    return pdb._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone()


def _node(eid, lat, lon, **tags):
    return {"type": "node", "id": eid, "lat": lat, "lon": lon, "tags": tags}


# ── find_station_name: nearest-WITH-label, not first-by-id ───────────────────

def test_nearest_named_wins_over_anonymous_first(monkeypatch):
    """Real OSM pattern: unnamed stall nodes (often lower ids) sit next to the named
    site POI — the label must come from the nearest element that HAS one."""
    els = [_node(1, 45.00004, 9.0),                              # ~5 m, anonymous
           _node(2, 45.00036, 9.0, operator="Ionity Binasco")]   # ~40 m, named
    monkeypatch.setattr(CL, "_query", lambda *a: els)
    assert CL.find_station_name(45.0, 9.0) == ("Ionity Binasco", None, True)


def test_way_mapped_station_found(monkeypatch):
    """Stations mapped as areas (way + center) must be usable — the PR's node-only
    query missed them entirely."""
    els = [{"type": "way", "id": 7, "center": {"lat": 45.0001, "lon": 9.0},
            "tags": {"name": "Supercharger Milano"}}]
    monkeypatch.setattr(CL, "_query", lambda *a: els)
    assert CL.find_station_name(45.0, 9.0) == ("Supercharger Milano", None, True)


def test_label_tag_priority(monkeypatch):
    els = [_node(1, 45.0, 9.0, operator="a2a", name="Colonnina Duomo")]
    monkeypatch.setattr(CL, "_query", lambda *a: els)
    assert CL.find_station_name(45.0, 9.0)[0] == "Colonnina Duomo"   # name beats operator


def test_nothing_found_vs_network_error(monkeypatch):
    monkeypatch.setattr(CL, "_query", lambda *a: [])
    assert CL.find_station_name(45.0, 9.0) == (None, None, True)  # OSM answered empty → sentinel
    # Transient only when EVERY applicable source errors (OSM + the Italian PUN here).
    monkeypatch.setattr(CL, "_query", lambda *a: None)
    monkeypatch.setattr(CL, "_pun_stations", lambda *a, **k: None)
    assert CL.find_station_name(45.0, 9.0) == (None, None, False)   # all dead → retry later


# ── sweep: labels, sentinels, skips, reuse, abort ─────────────────────────────

def test_sweep_labels_and_sentinels(tmp_path, monkeypatch):
    """Public charge near a station → named; one in the void → '' sentinel (resolved,
    never re-asked: the second sweep sees no candidates and makes zero calls)."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0)
    _charge(pdb, 2, lat=46.0, lon=10.0)
    calls = []
    def fake(lat, lon):
        calls.append((lat, lon))
        return ("E-Moving", None, True) if lat == 45.0 else (None, None, True)
    monkeypatch.setattr(CL, "find_station_name", fake)
    assert CL.sweep_now() == 1                       # one NAME found
    assert _row(pdb, 1)["location_name"] == "E-Moving"
    assert _row(pdb, 2)["location_name"] == ""       # looked up, nothing there
    assert len(calls) == 2
    assert CL.sweep_now() == 0 and len(calls) == 2   # fully resolved: no further calls


def test_sweep_auto_confirms_unambiguous_type(tmp_path, monkeypatch):
    """The station's own current+kW data, when unambiguous, fills in the AC/FAST/HPC
    type too — skipping the manual 'confirm this charge' step, exactly the feature the
    user asked for after the Fistra (MC) charge got stuck unlabelled AND unconfirmed."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0)     # location_type NULL — still unconfirmed
    monkeypatch.setattr(CL, "find_station_name", lambda *a: ("Enel X Way", "HPC", True))
    assert CL.sweep_now() == 1
    row = _row(pdb, 1)
    assert row["location_name"] == "Enel X Way"
    assert row["location_type"] == "HPC"


def test_sweep_does_not_override_an_already_confirmed_type(tmp_path, monkeypatch):
    """Naming and confirming are independent: a charge the user already typed MANUAL
    can still get its 📍 name, but auto-confirm must never overwrite that choice."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0, ctype="MANUAL")
    monkeypatch.setattr(CL, "find_station_name", lambda *a: ("Enel X Way", "HPC", True))
    assert CL.sweep_now() == 1
    row = _row(pdb, 1)
    assert row["location_name"] == "Enel X Way"   # still got named
    assert row["location_type"] == "MANUAL"       # but the manual pick stands


def test_sweep_leaves_ambiguous_type_unconfirmed(tmp_path, monkeypatch):
    """A mixed AC/DC site (or a source with no current-type data) can't say which cable
    THIS session used — the charge stays named but unconfirmed, same as before."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0)
    monkeypatch.setattr(CL, "find_station_name", lambda *a: ("Ionity Hub", None, True))
    assert CL.sweep_now() == 1
    row = _row(pdb, 1)
    assert row["location_name"] == "Ionity Hub"
    assert row["location_type"] is None


def test_sweep_reuse_path_does_not_guess_type(tmp_path, monkeypatch):
    """The GPS-reuse shortcut (skips the network call) copies only the NAME, never a
    type: the reused spot's own charge may have been typed by the user for unrelated
    reasons (MANUAL/FREE/a correction), so propagating it would be a guess, not a fact."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0, name="Ionity Binasco", ctype="MANUAL")
    _charge(pdb, 2, lat=45.00027, lon=9.0)                       # ~30 m away, unconfirmed
    monkeypatch.setattr(CL, "find_station_name",
                        lambda *a: (_ for _ in ()).throw(AssertionError("network hit")))
    assert CL.sweep_now() == 1
    row = _row(pdb, 2)
    assert row["location_name"] == "Ionity Binasco"
    assert row["location_type"] is None


def test_classify_current():
    assert CL._classify_current("AC", 22.0) == "AC"
    assert CL._classify_current("AC", 0.0) == "AC"
    assert CL._classify_current("DC", 50.0) == "FAST"
    assert CL._classify_current("DC", 150.0) == "HPC"
    assert CL._classify_current("DC", 0.0) == "FAST"     # unknown kW → the common default
    assert CL._classify_current("AC/DC", 150.0) is None  # mixed site → ambiguous
    assert CL._classify_current("", 0.0) is None         # source didn't say


def test_sweep_skips_home_wallbox_open_and_nogps(tmp_path, monkeypatch):
    """Home charges must never be sent out — by HOME type OR wallbox session evidence —
    and open/GPS-less ones aren't candidates either."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME")                    # user/auto-confirmed home
    _charge(pdb, 2, ac=7.4)                          # wallbox-billed energy
    _charge(pdb, 3, wb_start=1234.5)                 # wallbox baseline seen at start
    _charge(pdb, 4, ended=None)                      # still charging
    _charge(pdb, 5, lat=None, lon=None)              # no GPS fix
    monkeypatch.setattr(CL, "find_station_name",
                        lambda *a: (_ for _ in ()).throw(AssertionError("network hit")))
    assert not db_reader.has_location_lookup_candidates()
    assert CL.sweep_now() == 0
    for cid in (1, 2, 3, 4, 5):
        assert _row(pdb, cid)["location_name"] is None


def test_sweep_reuses_nearby_label_without_network(tmp_path, monkeypatch):
    """A charge ~30 m from an already-labelled one is the same station → copy the
    label, zero Overpass calls."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0, name="Ionity Binasco")    # resolved earlier
    _charge(pdb, 2, lat=45.00027, lon=9.0)                       # ~30 m away
    monkeypatch.setattr(CL, "find_station_name",
                        lambda *a: (_ for _ in ()).throw(AssertionError("network hit")))
    assert CL.sweep_now() == 1
    assert _row(pdb, 2)["location_name"] == "Ionity Binasco"


def test_sweep_aborts_on_transient_error(tmp_path, monkeypatch):
    """Overpass down → stop the round, leave NULL so the next sweep retries."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1)
    monkeypatch.setattr(CL, "find_station_name", lambda *a: (None, None, False))
    assert CL.sweep_now() == 0
    assert _row(pdb, 1)["location_name"] is None
    assert db_reader.has_location_lookup_candidates()


def test_maybe_sweep_guards(tmp_path, monkeypatch):
    """Render hook: no thread when the toggle is off, when TTL is fresh, or with no
    candidates; thread when on + stale + candidates."""
    pdb = _setup(tmp_path, monkeypatch)
    spawned = []
    class FakeThread:
        def __init__(self, target=None, daemon=None):
            spawned.append(target)
        def start(self):
            pass
    monkeypatch.setattr(CL.threading, "Thread", FakeThread)
    _charge(pdb, 1)
    CL.maybe_sweep()                                             # toggle off (default)
    assert spawned == []
    db_reader.set_setting("charger_locator", "1")
    db_reader.set_setting("charger_locator_swept_at", "99999999999")   # fresh TTL
    CL.maybe_sweep()
    assert spawned == []
    db_reader.set_setting("charger_locator_swept_at", "0")
    CL.maybe_sweep()                                             # on + stale + candidate
    assert spawned == [CL.sweep_now]


# ── find_nearby (Navigation page) ─────────────────────────────────────────────

def test_find_nearby_sorted_with_generic_and_info(monkeypatch):
    els = [_node(1, 45.009, 9.0, name="Far One"),                       # ~1 km
           _node(2, 45.0009, 9.0, **{"socket:ccs": "2", "socket:type2": "2",
                                     "socket:ccs:output": "300 kW"}),   # ~100 m, unnamed
           {"type": "way", "id": 3, "center": {"lat": 45.0045, "lon": 9.0},
            "tags": {"operator": "Enel X Way"}}]                        # ~500 m
    monkeypatch.setattr(CL, "_query", lambda *a: els)
    res = CL.find_nearby(45.0, 9.0, 2000)
    assert [s["name"] for s in res] == [None, "Enel X Way", "Far One"]  # nearest first
    assert res[0]["info"] == "AC/DC · CCS · Type 2 · 300 kW"            # inferred current
    assert res[0]["dist_m"] < res[1]["dist_m"] < res[2]["dist_m"]
    # transient error: every applicable source dead (OSM + Italian PUN; OCM not keyed)
    monkeypatch.setattr(CL, "_query", lambda *a: None)
    monkeypatch.setattr(CL, "_pun_stations", lambda *a, **k: None)
    assert CL.find_nearby(45.0, 9.0, 2000) is None


def test_find_nearby_dedupes_site_columns(monkeypatch):
    """A site's individual charge_point columns (same operator, metres apart) and an
    anonymous stall right next to them collapse into ONE pin — nearest kept, and the
    stall's socket info enriches it when the kept entry had none."""
    els = [_node(1, 45.0001, 9.0, operator="Enel X"),                   # ~11 m (kept)
           _node(2, 45.0003, 9.0, operator="Enel X",
                 **{"socket:type2": "2"}),                              # ~33 m, same label
           _node(3, 45.00035, 9.0),                                     # ~39 m, anonymous
           _node(4, 45.003, 9.0, operator="Be Charge")]                 # ~330 m, different
    monkeypatch.setattr(CL, "_query", lambda *a: els)
    res = CL.find_nearby(45.0, 9.0, 2000)
    assert [s["name"] for s in res] == ["Enel X", "Be Charge"]
    assert res[0]["info"] == "AC · Type 2"                              # enriched from #2


def test_current_type_inference():
    assert CL._socket_info({"socket:type2": "2"}) == "AC · Type 2"
    assert CL._socket_info({"socket:ccs": "1"}) == "DC · CCS"
    assert CL._socket_info({"maxoutput": "150"}) == "DC · 150 kW"       # ≥50 kW ⇒ DC
    assert CL._socket_info({"maxoutput": "22"}) == "22 kW"              # ambiguous: no guess
    assert CL._socket_info({}) == ""


# ── Open Charge Map (optional keyed source, merged with OSM) ──────────────────

def _ocm_poi(lat, lon, title, kw=22.0, current=20):
    """Shape taken from the real ocm-export dump (AddressInfo + Connections)."""
    return {"AddressInfo": {"Title": title, "Latitude": lat, "Longitude": lon},
            "OperatorInfo": {"Title": "Op " + title},
            "Connections": [{"PowerKW": kw, "CurrentTypeID": current}]}


def test_ocm_stations_parse(monkeypatch):
    import io
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    class FakeResp(io.StringIO):
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    import json as J
    body = J.dumps([_ocm_poi(45.0026, 9.0, "BeCharge Lorenteggio", 22.0, 20),
                    _ocm_poi(45.02, 9.0, "Too Far", 50.0, 30),          # outside radius
                    _ocm_poi(45.0009, 9.0, "Ionity Hub", 300.0, 30)])
    monkeypatch.setattr(CL.urllib.request, "urlopen",
                        lambda req, timeout=0: FakeResp(body))
    res = CL._ocm_stations(45.0, 9.0, 1500)
    assert [s["name"] for s in res] == ["BeCharge Lorenteggio", "Ionity Hub"]
    assert res[1]["info"] == "DC · 300 kW"                              # CurrentTypeID 30
    assert res[0]["info"] == "AC · 22 kW"                               # CurrentTypeID 20
    assert res[1]["current"] == "DC" and res[1]["kw"] == 300.0
    assert res[0]["current"] == "AC" and res[0]["kw"] == 22.0


def test_ocm_keyless_is_silent_and_error_is_none(monkeypatch):
    assert CL._ocm_stations(45.0, 9.0, 1000) == []                      # no key → no call
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    def boom(req, timeout=0):
        raise OSError("403")
    monkeypatch.setattr(CL.urllib.request, "urlopen", boom)
    assert CL._ocm_stations(45.0, 9.0, 1000) is None                    # transient error


def test_tomtom_stations_parse(monkeypatch):
    """TomTom Category Search → station dicts: name, AC/DC + max kW from connectors,
    distance from the API's own `dist`. [] without a key, None on error."""
    import io, json as J
    class R(io.StringIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    assert CL._tomtom_stations(45.0, 9.0, 2000) == []          # no key → silent, no call
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "k")
    body = {"results": [
        {"poi": {"name": "Ionity Milano"}, "position": {"lat": 45.01, "lon": 9.0}, "dist": 1110.0,
         "chargingPark": {"connectors": [
             {"currentType": "DC", "ratedPowerKW": 350},
             {"currentType": "AC3", "ratedPowerKW": 22}]}},
        {"poi": {"name": "No Coords"}, "dist": 5},                # dropped (no position)
    ]}
    monkeypatch.setattr(CL.urllib.request, "urlopen", lambda req, timeout=0: R(J.dumps(body)))
    res = CL._tomtom_stations(45.0, 9.0, 2000)
    assert [s["name"] for s in res] == ["Ionity Milano"]
    assert res[0]["info"] == "AC/DC · 350 kW"                  # both currents + max kW
    assert res[0]["dist_m"] == 1110                            # from TomTom's own dist

    def boom(req, timeout=0):
        raise OSError("timeout")
    monkeypatch.setattr(CL.urllib.request, "urlopen", boom)
    assert CL._tomtom_stations(45.0, 9.0, 2000) is None        # transient error


def test_find_nearby_merges_four_sources(monkeypatch):
    """All four sources merge and dedupe. TomTom (keyed) joins the live view."""
    monkeypatch.setattr(CL, "_query", lambda *a: [_node(1, 45.02, 9.0, operator="Enel X")])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "Lidl", "lat": 45.004, "lon": 9.0,
                                          "dist_m": 445, "info": "AC · 22 kW"}])
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "k")
    monkeypatch.setattr(CL, "_tomtom_stations",
                        lambda *a, **k: [{"name": "Ewiva", "lat": 45.001, "lon": 9.0,
                                         "dist_m": 111, "info": "DC · 300 kW"}])
    monkeypatch.setattr(CL, "_pun_stations",
                        lambda *a, **k: [{"name": "A2A", "lat": 45.003, "lon": 9.0,
                                          "dist_m": 333, "info": "AC · 22 kW", "avail": "4/4"}])
    res = CL.find_nearby(45.0, 9.0, 5000, limit=25)
    assert [s["name"] for s in res] == ["Ewiva", "A2A", "Lidl", "Enel X"]   # all 4, nearest first


def test_label_sweep_never_uses_tomtom(monkeypatch):
    """TomTom forbids storing its data → the saved 📍 label path must NOT query it,
    even with a key set (find_station_name has no TomTom call by construction)."""
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "k")
    monkeypatch.setattr(CL, "_tomtom_stations",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("TomTom in label path")))
    monkeypatch.setattr(CL, "_query", lambda *a: [_node(1, 45.0, 9.0, operator="A2A")])
    assert CL.find_station_name(45.0, 9.0) == ("A2A", None, True)    # OSM name, no TomTom touched


def test_find_nearby_merges_osm_and_ocm(monkeypatch):
    """The two sources complement each other (validated on real data near Silvio's
    home): the merge shows BOTH, sorted, and dedupes the same physical station."""
    osm = [_node(1, 45.0115, 9.0, operator="Enel X")]                          # ~1.3 km
    ocm = [{"name": "Lidl Lorenteggio", "lat": 45.0026, "lon": 9.0, "dist_m": 286, "info": "AC · 22 kW"},
           {"name": "Enel X Milano", "lat": 45.01151, "lon": 9.0, "dist_m": 1280, "info": "AC · 22 kW"}]
    monkeypatch.setattr(CL, "_query", lambda *a: osm)
    monkeypatch.setattr(CL, "_ocm_stations", lambda *a, **k: ocm)
    res = CL.find_nearby(45.0, 9.0, 2000)
    assert [s["name"] for s in res] == ["Lidl Lorenteggio", "Enel X"]   # dup merged (~1 m apart)
    assert res[1]["info"] == "AC · 22 kW"                               # enriched from OCM
    # OSM down but OCM healthy → still serve OCM results instead of an error
    monkeypatch.setattr(CL, "_query", lambda *a: None)
    assert [s["name"] for s in CL.find_nearby(45.0, 9.0, 2000)] == ["Lidl Lorenteggio", "Enel X Milano"]


def test_operator_filter_narrows_pun_and_results(monkeypatch):
    """Operator filter (e.g. 'electra'): PUN is narrowed server-side to the matching CPO
    code(s), and the merged result keeps only matching names — so a specific far network
    surfaces past the nearest-N that would otherwise bury it."""
    assert CL._pun_op_codes("electra") == ["ELC"]
    assert set(CL._pun_op_codes("ionity")) == {"ION", "IOY"}
    assert CL._pun_op_codes("nonsense brand") is None

    seen = {}
    def fake_pun(lat, lon, radius_m, op_codes=None, **k):
        seen["op_codes"] = op_codes
        return [{"name": "Electra", "lat": 45.04, "lon": 9.0, "dist_m": 4943,
                 "info": "DC · 150 kW", "avail": "3/4"}]
    monkeypatch.setattr(CL, "_pun_stations", fake_pun)
    # OSM returns a near A2A that must be filtered OUT when the user asked for Electra
    monkeypatch.setattr(CL, "_query",
                        lambda *a: [_node(1, 45.003, 9.0, operator="A2A")])
    res = CL.find_nearby(45.0, 9.0, 10000, limit=60, name_filter="electra")
    assert seen["op_codes"] == ["ELC"]                     # PUN narrowed server-side
    assert [s["name"] for s in res] == ["Electra"]         # A2A filtered out by name


def test_station_name_uses_ocm_and_osm_dead_rules(monkeypatch):
    monkeypatch.setattr(CL, "_query", lambda *a: None)                  # OSM dead
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "BeCharge", "lat": 45.0, "lon": 9.0,
                                          "dist_m": 60, "info": ""}])
    assert CL.find_station_name(45.0, 9.0) == ("BeCharge", None, True)  # OCM name wins
    # every applicable source errors → retry (OSM None, OCM keyed+None, PUN None)
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations", lambda *a, **k: None)
    monkeypatch.setattr(CL, "_pun_stations", lambda *a, **k: None)
    assert CL.find_station_name(45.0, 9.0) == (None, None, False)


# ── PUN — Piattaforma Unica Nazionale (Italy, keyless, referer-gated) ─────────

def _pun_feat(loc, evse, cur, kw, stato, lat, lon, nome=None):
    return {"attributes": {"ID_location": loc, "ID_EVSE": evse,
                           "Nome_location": nome or loc,
                           "Tipologia_di_alimentazione": cur,
                           "Potenza_erogabile": kw, "Stato": stato,
                           "Latitudine_EVSE": lat, "Longitudine_EVSE": lon}}


def test_pun_groups_connectors_and_reads_status(monkeypatch):
    """Per-connector rows collapse to one site (ID_location); operator from the EVSE
    prefix, AC/DC + max kW + live availability aggregated. Real Milan-area shape."""
    import io, json as J
    class R(io.StringIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    feats = [
        _pun_feat("IT00364", "IT*ATE*E1*1", "DC", 60000, "AVAILABLE", 45.431, 9.125),
        _pun_feat("IT00364", "IT*ATE*E1*2", "DC", 60000, "CHARGING",  45.431, 9.125),
        _pun_feat("LOC1",    "IT*A2A*E2*1", "AC_3_PHASE", 22000, "AVAILABLE", 45.446, 9.126, "Piazza X"),
    ]
    monkeypatch.setattr(CL.urllib.request, "urlopen",
                        lambda req, timeout=0: R(J.dumps({"features": feats})))
    res = _REAL_PUN(45.44, 9.12, 1500)
    by = {s["name"]: s for s in res}
    assert set(by) == {"Atlante", "A2A"}
    assert by["Atlante"]["info"] == "DC · 60 kW"
    assert by["Atlante"]["avail"] == "1/2"          # one AVAILABLE of two connectors
    assert by["A2A"]["info"] == "AC · 22 kW"
    assert by["A2A"]["avail"] == "1/1"
    assert by["Atlante"]["current"] == "DC" and by["Atlante"]["kw"] == 60.0
    assert by["A2A"]["current"] == "AC" and by["A2A"]["kw"] == 22.0


def test_pun_skipped_outside_italy(monkeypatch):
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("PUN hit abroad"))
    monkeypatch.setattr(CL.urllib.request, "urlopen", boom)
    assert _REAL_PUN(51.5, -0.12, 1500) == []   # London → no call, []
    assert CL._in_italy(45.46, 9.19) and not CL._in_italy(51.5, -0.12)


def test_pun_error_is_none(monkeypatch):
    def boom(req, timeout=0):
        raise OSError("403")
    monkeypatch.setattr(CL.urllib.request, "urlopen", boom)
    assert _REAL_PUN(45.44, 9.12, 1500) is None   # in Italy + error → retryable


def test_pun_shrinks_radius_when_cap_hit(monkeypatch):
    """The bug behind 'widening hides the nearest': the server truncates at
    maxRecordCount in OBJECTID order. When a fetch fills the cap we halve the radius
    and refetch until the set is complete, so the nearest are never dropped."""
    calls = []
    far = {"attributes": {"ID_location": "FAR", "ID_EVSE": "IT*ENX*EF*1",
                          "Tipologia_di_alimentazione": "DC", "Potenza_erogabile": 90000,
                          "Stato": "AVAILABLE", "Latitudine_EVSE": 45.50, "Longitudine_EVSE": 9.30}}
    near = {"attributes": {"ID_location": "NEAR", "ID_EVSE": "IT*A2A*EN*1",
                           "Tipologia_di_alimentazione": "AC_3_PHASE", "Potenza_erogabile": 22000,
                           "Stato": "AVAILABLE", "Latitudine_EVSE": 45.441, "Longitudine_EVSE": 9.121}}

    def fake_query(lat, lon, radius_m, max_fetch):
        calls.append(radius_m)
        # First (big-radius) call fills the cap with FAR-only rows (OID order, no NEAR);
        # after shrinking, the complete small set finally includes the NEAR site.
        if radius_m > 3000:
            return [far] * max_fetch
        return [near, far]

    monkeypatch.setattr(CL, "_pun_query", fake_query)
    res = _REAL_PUN(45.44, 9.12, 10000, max_fetch=50)
    assert len(calls) >= 2 and calls[0] > calls[-1]          # it shrank
    assert any(s["name"] == "A2A" for s in res)              # the nearest finally surfaced


def test_find_nearby_merges_all_three(monkeypatch):
    """OSM + OCM + PUN merged and deduped: the same physical site seen by two sources
    is one pin, and PUN's nearby Italian stations show with their live status carried."""
    osm = [_node(1, 45.013, 9.0, operator="Enel X")]
    monkeypatch.setattr(CL, "_query", lambda *a: osm)
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "Lidl", "lat": 45.004, "lon": 9.0,
                                          "dist_m": 445, "info": "AC · 22 kW"}])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_pun_stations",
                        lambda *a, **k: [{"name": "A2A", "lat": 45.0034, "lon": 9.0,
                                          "dist_m": 378, "info": "AC · 22 kW", "avail": "4/4"},
                                         {"name": "Enel X", "lat": 45.01301, "lon": 9.0,
                                          "dist_m": 1446, "info": "DC · 90 kW", "avail": "2/2"}])
    res = CL.find_nearby(45.0, 9.0, 2000)
    assert [s["name"] for s in res] == ["A2A", "Lidl", "Enel X"]   # nearest first, Enel X deduped
    assert res[0]["avail"] == "4/4"                                # PUN live status preserved
    assert res[2]["info"] == "DC · 90 kW"   # the same Enel X from OSM+PUN merged, richer kept
