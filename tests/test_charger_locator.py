"""📍 charging-station labels + Navigation nearby search (idea: @hubcasale, PR #48,
reimplemented web-side). The Overpass network seam (`_query` / `find_station_candidates`)
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
            ctype=None, ac=None, wb_start=None, name=None, url=None):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, latitude, longitude, location_type, ac_energy_kwh,"
        " wallbox_energy_start_kwh, location_name, location_url)"
        " VALUES (?,1,'2026-06-02T16:48:39+00:00',?,40,52,8.0,?,?,?,?,?,?,?)",
        (cid, ended, lat, lon, ctype, ac, wb_start, name, url))
    pdb._conn.commit()


def _row(pdb, cid):
    return pdb._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone()


def _node(eid, lat, lon, **tags):
    return {"type": "node", "id": eid, "lat": lat, "lon": lon, "tags": tags}


# ── find_station_candidates: nearest-WITH-label wins, not first-by-id ────────
# Backs BOTH the background sweep (takes options[0] deterministically — see
# _sweep_body) and the manual 📍 recalc button (shows a picker when ambiguous).

def test_candidates_nearest_named_wins_over_anonymous_first(monkeypatch):
    """Real OSM pattern: unnamed stall nodes (often lower ids) sit next to the named
    site POI — the label must come from the nearest element that HAS one."""
    els = [_node(1, 45.00004, 9.0),                              # ~5 m, anonymous
           _node(2, 45.00036, 9.0, operator="Ionity Binasco")]   # ~40 m, named
    monkeypatch.setattr(CL, "_query", lambda *a: els)
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert [o["name"] for o in options] == ["Ionity Binasco"]


def test_candidates_way_mapped_station_found(monkeypatch):
    """Stations mapped as areas (way + center) must be usable — the PR's node-only
    query missed them entirely."""
    els = [{"type": "way", "id": 7, "center": {"lat": 45.0001, "lon": 9.0},
            "tags": {"name": "Supercharger Milano"}}]
    monkeypatch.setattr(CL, "_query", lambda *a: els)
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert [o["name"] for o in options] == ["Supercharger Milano"]


def test_candidates_label_tag_priority(monkeypatch):
    els = [_node(1, 45.0, 9.0, operator="a2a", name="Colonnina Duomo")]
    monkeypatch.setattr(CL, "_query", lambda *a: els)
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert options[0]["name"] == "Colonnina Duomo"   # name beats operator


def test_candidates_uses_ocm_when_osm_dead_and_retries_when_all_dead(monkeypatch):
    """OSM dead but OCM healthy → OCM's name is used. Only when EVERY applicable
    source errors (OSM + the keyed OCM + the Italian PUN here) is it a transient
    failure worth retrying — not a genuine 'nothing found'."""
    monkeypatch.setattr(CL, "_query", lambda *a: None)                  # OSM dead
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "BeCharge", "lat": 45.0, "lon": 9.0,
                                          "dist_m": 60, "info": ""}])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert [o["name"] for o in options] == ["BeCharge"]
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")   # OCM now counts toward "attempted"
    monkeypatch.setattr(CL, "_ocm_stations", lambda *a, **k: None)
    monkeypatch.setattr(CL, "_pun_stations", lambda *a, **k: None)
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert options == [] and ok is False               # all dead → retry later


# ── find_station_candidates: the manual 📍 recalc button, ambiguity surfaced ──

def test_candidates_single_match_auto_applies(monkeypatch):
    """One named station within range, one source → a single option (the caller,
    sweep or manual button alike, applies it directly — no ambiguity)."""
    monkeypatch.setattr(CL, "_query", lambda *a: [_node(1, 45.0001, 9.0, operator="Enel X")])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert [o["name"] for o in options] == ["Enel X"]


def test_candidates_agreeing_sources_merge_not_ambiguous(monkeypatch):
    """Same name, one source has details the other lacks (not a real conflict) →
    still ONE option, richer-merged — no popup needed."""
    monkeypatch.setattr(CL, "_query", lambda *a: [_node(1, 45.0001, 9.0, operator="Enel X")])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "Enel X", "lat": 45.0002, "lon": 9.0,
                                          "dist_m": 22, "info": "AC · 22 kW", "address": "Via Test 1"}])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert len(options) == 1
    assert options[0]["info"] == "AC · 22 kW"
    assert options[0]["address"] == "Via Test 1"


def test_candidates_conflicting_details_are_ambiguous(monkeypatch):
    """Same name, sources disagree on the actual detail (different rated power) →
    both variants surface so the user can pick, instead of silently trusting one."""
    monkeypatch.setattr(CL, "_query",
                        lambda *a: [_node(1, 45.0001, 9.0, operator="Enel X", **{"maxoutput": "22"})])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "Enel X", "lat": 45.0002, "lon": 9.0,
                                          "dist_m": 22, "info": "DC · 300 kW", "address": None}])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert len(options) == 2
    assert {o["info"] for o in options} == {"22 kW", "DC · 300 kW"}
    assert all(o["name"] == "Enel X" for o in options)


def test_candidates_diff_name_within_site_radius_is_a_choice(monkeypatch):
    """Regression (real charge, see charge #132/#93): a different name from another
    source, close enough to be the SAME physical site (within
    _SITE_DIFF_NAME_RADIUS_M — widened past the old 25 m because real service-area
    sources land 70+ m apart), must surface as a choice for the manual recalc button
    — not get silently merged into a single, possibly-renamed pin. The unattended
    sweep is unaffected: it always takes options[0], nearest-first."""
    monkeypatch.setattr(CL, "_query", lambda *a: [
        _node(1, 45.0005, 9.0, operator="Enel X"),
        _node(2, 45.0010, 9.0, operator="BeCharge"),   # ~56 m from Enel X — same site now
    ])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert [o["name"] for o in options] == ["Enel X", "BeCharge"]   # nearest first


def test_candidates_distance_wins_over_a_genuinely_farther_different_name(monkeypatch):
    """Distance still decides first: a different named station well beyond
    _SITE_DIFF_NAME_RADIUS_M never creates a choice by itself — only the NEAREST
    site's own data (now possibly spanning several close-by different names) can be
    ambiguous."""
    monkeypatch.setattr(CL, "_query", lambda *a: [
        _node(1, 45.0005, 9.0, operator="Enel X"),
        _node(2, 45.0020, 9.0, operator="BeCharge"),   # ~167 m away — a genuinely different site
    ])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert [o["name"] for o in options] == ["Enel X"]   # nearest wins outright, no popup


def test_candidates_conflict_only_on_the_nearest_site(monkeypatch):
    """A genuinely farther station's conflicting details don't matter — only the
    nearest site's own conflict (if any) can trigger the popup."""
    monkeypatch.setattr(CL, "_query", lambda *a: [
        _node(1, 45.0005, 9.0, operator="Enel X"),                          # nearest, no info
        _node(2, 45.0020, 9.0, operator="BeCharge", **{"maxoutput": "22"}),  # far, has info
    ])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "BeCharge", "lat": 45.0021, "lon": 9.0,
                                          "dist_m": 233, "info": "DC · 300 kW", "address": None}])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert [o["name"] for o in options] == ["Enel X"]   # nearest site, no conflict of its own


def test_candidates_same_site_different_names_across_sources_is_one_site(monkeypatch):
    """Regression (found on real data): a service area is routinely reported under a
    DIFFERENT name by each source — OSM tags the specific network ('Free To X'), PUN
    gives the generic site name ('Area di Servizio - Sant'Ilario Nord') — a few metres
    apart. Grouping by bare name equality treated these as two competing sites and
    picked whichever was a hair closer, silently DISCARDING the other (here, OSM's own
    name+link lost to PUN's slightly-nearer generic label). Proximity must win: same
    physical site → one cluster, and different names within it ALWAYS surface as
    choices (not just when their power/connector details also happen to conflict —
    see test_candidates_diff_name_within_site_radius_is_a_choice)."""
    monkeypatch.setattr(CL, "_query", lambda *a: [
        _node(1, 44.3933016, 9.0463942, name="Free To X",
              **{"socket:type2_combo": "4", "socket:type2_combo:output": "HPC 300kW"}),
    ])
    monkeypatch.setattr(CL, "_pun_stations",
                        lambda *a, **k: [{"name": "Area di Servizio - Sant'Ilario Nord",
                                          "lat": 44.39337, "lon": 9.04656, "dist_m": 10,
                                          "info": "AC/DC · 300 kW", "avail": "7/7"}])
    options, ok = CL.find_station_candidates(44.393365, 9.046696)
    assert ok is True
    assert len(options) == 2                                      # NOT silently discarded
    names = {o["name"] for o in options}
    assert names == {"Free To X", "Area di Servizio - Sant'Ilario Nord"}
    osm_opt = next(o for o in options if o["name"] == "Free To X")
    assert osm_opt["url"] == "https://www.openstreetmap.org/node/1"   # the richer one is choosable


def test_candidates_nearest_keeps_its_name_but_borrows_link_from_the_other(monkeypatch):
    """Regression (real charge #132, 'Plenitude'): PUN's name is 1 m from the charge,
    OCM's differently-named, richer-LOOKING record ('BeCharge...', has address+url) is
    22 m away — same info, so under the OLD richness-based merge OCM would win as
    'base' and silently RENAME the charge. Now: different names never merge, so the
    nearest (PUN, 'Plenitude') stays options[0] — what the unattended sweep applies —
    and it still gets OCM's link borrowed onto it, even though PUN itself has none."""
    monkeypatch.setattr(CL, "_query", lambda *a: [])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "BeCharge CorridoMnia Shopping Park",
                                          "lat": 45.00019, "lon": 9.0, "dist_m": 22,
                                          "info": "AC · 22 kW", "address": "Via Test 1",
                                          "url": "https://openchargemap.org/poi/details/1"}])
    monkeypatch.setattr(CL, "_pun_stations",
                        lambda *a, **k: [{"name": "Plenitude", "lat": 45.00001, "lon": 9.0,
                                          "dist_m": 1, "info": "AC · 22 kW", "avail": "8/8"}])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert [o["name"] for o in options] == ["Plenitude", "BeCharge CorridoMnia Shopping Park"]
    assert options[0]["url"] == "https://openchargemap.org/poi/details/1"   # borrowed, not its own


def test_candidates_link_borrowed_even_when_farther_source_has_no_own_name_conflict(monkeypatch):
    """Regression (real charge #93, 'Area di Servizio - Flaminia Ovest'): OCM's own
    matching record ('Free To X ADS Flaminia Ovest') sits 72 m away — outside the old
    25 m different-name radius, so it used to be discarded as 'a different, farther
    site' and the PUN name never got a link at all. Within the new wider
    _SITE_DIFF_NAME_RADIUS_M it's now the same site: both names are choosable, and
    the nearest (PUN) option — what the sweep applies unattended — has OCM's link."""
    monkeypatch.setattr(CL, "_query", lambda *a: [])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "Free To X ADS Flaminia Ovest",
                                          "lat": 42.317265, "lon": 12.491034, "dist_m": 70,
                                          "info": "DC · 300 kW", "address": "A1 km 509",
                                          "url": "https://openchargemap.org/poi/details/2"}])
    monkeypatch.setattr(CL, "_pun_stations",
                        lambda *a, **k: [{"name": "Area di Servizio - Flaminia Ovest",
                                          "lat": 42.31664, "lon": 12.49127, "dist_m": 2,
                                          "info": "AC/DC · 300 kW", "avail": "7/7"}])
    options, ok = CL.find_station_candidates(42.316657, 12.491259)
    assert ok is True
    assert options[0]["name"] == "Area di Servizio - Flaminia Ovest"   # nearest, sweep applies it
    assert options[0]["url"] == "https://openchargemap.org/poi/details/2"   # borrowed
    assert [o["name"] for o in options][1] == "Free To X ADS Flaminia Ovest"


def test_candidates_none_found_vs_transient_error(monkeypatch):
    monkeypatch.setattr(CL, "_query", lambda *a: [])
    assert CL.find_station_candidates(45.0, 9.0) == ([], True)     # answered, nothing there
    monkeypatch.setattr(CL, "_query", lambda *a: None)
    monkeypatch.setattr(CL, "_pun_stations", lambda *a, **k: None)
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert options == [] and ok is False                           # every source dead → retry


# ── sweep: labels, sentinels, skips, reuse, abort ─────────────────────────────

def test_sweep_labels_and_sentinels(tmp_path, monkeypatch):
    """Public charge near a station → named (with its link); one in the void → ''
    sentinel (resolved, never re-asked: the second sweep sees no candidates and makes
    zero calls). Unattended automation takes options[0] deterministically — it can't
    pop up a picker like the manual 📍 button does."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0)
    _charge(pdb, 2, lat=46.0, lon=10.0)
    calls = []
    def fake(lat, lon):
        calls.append((lat, lon))
        if lat == 45.0:
            return ([{"name": "E-Moving", "url": "https://www.openstreetmap.org/node/1"}], True)
        return ([], True)
    monkeypatch.setattr(CL, "find_station_candidates", fake)
    assert CL.sweep_now() == 1                       # one NAME found
    assert _row(pdb, 1)["location_name"] == "E-Moving"
    assert _row(pdb, 1)["location_url"] == "https://www.openstreetmap.org/node/1"
    assert _row(pdb, 2)["location_name"] == ""       # looked up, nothing there
    assert _row(pdb, 2)["location_url"] is None
    assert len(calls) == 2
    assert CL.sweep_now() == 0 and len(calls) == 2   # fully resolved: no further calls


def test_get_charge_location_for_manual_recalc(tmp_path, monkeypatch):
    """db_reader.get_charge_location backs the 📍 recalc button: unlike
    get_location_lookup_candidates (only NOT-yet-labelled charges, for the sweep),
    it fetches ANY charge by id — including one already labelled, so a user can
    force a fresh lookup on it."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0, ctype="AC", name="Old Name",
            url="https://openstreetmap.org/node/1")
    row = db_reader.get_charge_location(1)
    assert row == {"id": 1, "latitude": 45.0, "longitude": 9.0, "location_type": "AC",
                   "location_name": "Old Name", "location_url": "https://openstreetmap.org/node/1"}
    assert db_reader.get_charge_location(999) is None


# ── backfill_urls_now: recover a link on ALREADY-labelled charges ────────────

def test_backfill_fills_link_when_name_still_matches(tmp_path, monkeypatch):
    """A charge labelled before location_url existed gets the link filled in — the
    saved name is left exactly as-is (only location_url is touched)."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0, ctype="AC", name="Enel X")   # no url yet
    monkeypatch.setattr(CL, "find_station_candidates",
                        lambda lat, lon: ([{"name": "Enel X", "info": "AC · 22 kW",
                                            "url": "https://www.openstreetmap.org/node/1",
                                            "dist_m": 20}], True))
    assert CL.backfill_urls_now() == 1
    row = _row(pdb, 1)
    assert row["location_name"] == "Enel X"                         # untouched
    assert row["location_url"] == "https://www.openstreetmap.org/node/1"


def test_backfill_skips_when_resolved_name_no_longer_matches(tmp_path, monkeypatch):
    """The surroundings resolve to a DIFFERENT name now (or the user hand-picked one
    from an old ambiguity popup that no source currently confirms) → skip rather than
    silently relabel or attach a mismatched link."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0, ctype="AC", name="Old Chosen Name")
    monkeypatch.setattr(CL, "find_station_candidates",
                        lambda lat, lon: ([{"name": "Different Name", "info": "",
                                            "url": "https://www.openstreetmap.org/node/9",
                                            "dist_m": 20}], True))
    assert CL.backfill_urls_now() == 0
    row = _row(pdb, 1)
    assert row["location_name"] == "Old Chosen Name"
    assert row["location_url"] is None


def test_backfill_stops_on_transient_error(tmp_path, monkeypatch):
    """Overpass down → stop the round; the charge stays queued for next time."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0, ctype="AC", name="Enel X")
    monkeypatch.setattr(CL, "find_station_candidates", lambda lat, lon: ([], False))
    assert CL.backfill_urls_now() == 0
    assert _row(pdb, 1)["location_url"] is None
    assert db_reader.get_labelled_charges_missing_url() != []


def test_backfill_ignores_home_and_already_linked_charges(tmp_path, monkeypatch):
    """HOME charges never get a location_name (so they're never in this queue anyway)
    and a charge that already has a link is left alone — the queue only ever holds
    named-but-link-less public charges."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME")                                            # no name at all
    _charge(pdb, 2, lat=45.0, lon=9.0, ctype="AC", name="Enel X",
            url="https://www.openstreetmap.org/node/1")                      # already linked
    assert db_reader.get_labelled_charges_missing_url() == []
    assert CL.backfill_urls_now() == 0


def test_sweep_skips_home_wallbox_open_and_nogps(tmp_path, monkeypatch):
    """Home charges must never be sent out — by HOME type OR wallbox session evidence —
    and open/GPS-less ones aren't candidates either."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HOME")                    # user/auto-confirmed home
    _charge(pdb, 2, ac=7.4)                          # wallbox-billed energy
    _charge(pdb, 3, wb_start=1234.5)                 # wallbox baseline seen at start
    _charge(pdb, 4, ended=None)                      # still charging
    _charge(pdb, 5, lat=None, lon=None)              # no GPS fix
    monkeypatch.setattr(CL, "find_station_candidates",
                        lambda *a: (_ for _ in ()).throw(AssertionError("network hit")))
    assert not db_reader.has_location_lookup_candidates()
    assert CL.sweep_now() == 0
    for cid in (1, 2, 3, 4, 5):
        assert _row(pdb, cid)["location_name"] is None


def test_sweep_reuses_nearby_label_without_network(tmp_path, monkeypatch):
    """A charge ~30 m from an already-labelled one is the same station → copy the
    label AND its link, zero Overpass calls."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0, name="Ionity Binasco",
            url="https://openstreetmap.org/node/1")                 # resolved earlier
    _charge(pdb, 2, lat=45.00027, lon=9.0)                           # ~30 m away
    monkeypatch.setattr(CL, "find_station_candidates",
                        lambda *a: (_ for _ in ()).throw(AssertionError("network hit")))
    assert CL.sweep_now() == 1
    assert _row(pdb, 2)["location_name"] == "Ionity Binasco"
    assert _row(pdb, 2)["location_url"] == "https://openstreetmap.org/node/1"


def test_sweep_aborts_on_transient_error(tmp_path, monkeypatch):
    """Overpass down → stop the round, leave NULL so the next sweep retries."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1)
    monkeypatch.setattr(CL, "find_station_candidates", lambda *a: ([], False))
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

def _ocm_poi(lat, lon, title, kw=22.0, current=20, poi_id=None,
            address_line1=None, town=None):
    """Shape taken from the real ocm-export dump (AddressInfo + Connections)."""
    return {"ID": poi_id,
            "AddressInfo": {"Title": title, "Latitude": lat, "Longitude": lon,
                            "AddressLine1": address_line1, "Town": town},
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
    body = J.dumps([_ocm_poi(45.0026, 9.0, "BeCharge Lorenteggio", 22.0, 20, poi_id=101,
                             address_line1="Via Lorenteggio 302", town="Milano"),
                    _ocm_poi(45.02, 9.0, "Too Far", 50.0, 30),          # outside radius
                    _ocm_poi(45.0009, 9.0, "Ionity Hub", 300.0, 30)])  # no address/ID
    monkeypatch.setattr(CL.urllib.request, "urlopen",
                        lambda req, timeout=0: FakeResp(body))
    res = CL._ocm_stations(45.0, 9.0, 1500)
    assert [s["name"] for s in res] == ["BeCharge Lorenteggio", "Ionity Hub"]
    assert res[1]["info"] == "DC · 300 kW"                              # CurrentTypeID 30
    assert res[0]["info"] == "AC · 22 kW"                               # CurrentTypeID 20
    assert res[0]["address"] == "Via Lorenteggio 302, Milano"
    assert res[1]["address"] is None
    assert res[0]["url"] == "https://openchargemap.org/poi/details/101"
    assert res[1]["url"] is None                        # no ID → no link


def test_ocm_keyless_is_silent_and_error_is_none(monkeypatch):
    assert CL._ocm_stations(45.0, 9.0, 1000) == []                      # no key → no call
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    def boom(req, timeout=0):
        raise OSError("403")
    monkeypatch.setattr(CL.urllib.request, "urlopen", boom)
    assert CL._ocm_stations(45.0, 9.0, 1000) is None                    # transient error


class _HTTPError(OSError):
    """Minimal stand-in for urllib.error.HTTPError — only the `.code` attribute the
    key-test functions actually read, so the fakes don't need the real exception's
    fp/hdrs/msg constructor dance."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def test_ocm_key_test_distinguishes_invalid_from_down(monkeypatch):
    import io
    class R(io.StringIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    assert CL.test_ocm_key("") == (False, "missing_key")            # no call for a blank key
    monkeypatch.setattr(CL.urllib.request, "urlopen", lambda req, timeout=0: R("[]"))
    assert CL.test_ocm_key("good-key") == (True, None)
    monkeypatch.setattr(CL.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(_HTTPError(403)))
    assert CL.test_ocm_key("bad-key") == (False, "invalid_key")
    monkeypatch.setattr(CL.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(OSError("timeout")))
    assert CL.test_ocm_key("good-key") == (False, "error")           # down, not necessarily wrong


def test_tomtom_key_test_distinguishes_invalid_from_down(monkeypatch):
    import io
    class R(io.StringIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    assert CL.test_tomtom_key("") == (False, "missing_key")
    monkeypatch.setattr(CL.urllib.request, "urlopen", lambda req, timeout=0: R('{"results":[]}'))
    assert CL.test_tomtom_key("good-key") == (True, None)
    monkeypatch.setattr(CL.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(_HTTPError(403)))
    assert CL.test_tomtom_key("bad-key") == (False, "invalid_key")
    monkeypatch.setattr(CL.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(OSError("timeout")))
    assert CL.test_tomtom_key("good-key") == (False, "error")


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
         "address": {"freeformAddress": "Via Ripamonti 88, Milano"},
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
    assert res[0]["address"] == "Via Ripamonti 88, Milano"     # no per-POI TomTom detail link

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


def test_tomtom_pin_borrows_ocm_link_first(monkeypatch):
    """TomTom has no public per-POI page — when OCM already covers the same spot
    (within link range), its detail-page URL is borrowed onto the TomTom pin.
    ~44 m apart: close enough for _borrow_url's 60 m link radius, but far enough
    (> the generic dedupe's 25 m different-name threshold) that this ISN'T the
    ordinary cross-source merge — an isolated check of the borrow itself."""
    monkeypatch.setattr(CL, "_query", lambda *a: [])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "Ionity SpA", "lat": 45.0104, "lon": 9.0,
                                          "dist_m": 1155, "info": "",
                                          "url": "https://openchargemap.org/poi/details/55"}])
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "k")
    monkeypatch.setattr(CL, "_tomtom_stations",
                        lambda *a, **k: [{"name": "Ionity Milano", "lat": 45.01, "lon": 9.0,
                                          "dist_m": 1110, "info": ""}])
    res = CL.find_nearby(45.0, 9.0, 5000, limit=25)
    assert len(res) == 2                                     # NOT merged (> 25 m, different names)
    tomtom_pin = next(s for s in res if s["name"] == "Ionity Milano")
    assert tomtom_pin["url"] == "https://openchargemap.org/poi/details/55"


def test_tomtom_pin_falls_back_to_osm_link(monkeypatch):
    """No OCM coverage for this spot → fall back to the OSM node/way page. Same
    ~44 m spacing rationale as the OCM test above."""
    monkeypatch.setattr(CL, "_query", lambda *a: [_node(9, 45.0204, 9.0, operator="BeCharge")])
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations", lambda *a, **k: [])   # no OCM coverage here
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "k")
    monkeypatch.setattr(CL, "_tomtom_stations",
                        lambda *a, **k: [{"name": "BeCharge TT", "lat": 45.02, "lon": 9.0,
                                          "dist_m": 2220, "info": ""}])
    res = CL.find_nearby(45.0, 9.0, 5000, limit=25)
    assert len(res) == 2                                     # NOT merged
    tomtom_pin = next(s for s in res if s["name"] == "BeCharge TT")
    assert tomtom_pin["url"] == "https://www.openstreetmap.org/node/9"


def test_tomtom_pin_no_link_when_uncovered(monkeypatch):
    """Neither OCM nor OSM cover this spot → the TomTom pin simply has no link."""
    monkeypatch.setattr(CL, "_query", lambda *a: [])
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "k")
    monkeypatch.setattr(CL, "_tomtom_stations",
                        lambda *a, **k: [{"name": "Solo Station", "lat": 45.05, "lon": 9.0,
                                          "dist_m": 5560, "info": ""}])
    res = CL.find_nearby(45.0, 9.0, 6000, limit=25)
    assert res[0]["url"] is None


def test_label_sweep_never_uses_tomtom(tmp_path, monkeypatch):
    """TomTom forbids storing its data → the saved 📍 label path must NOT query it,
    even with a key set (find_station_candidates has no TomTom call by construction).
    Exercised through the REAL sweep (not the pure function directly), since that's
    the actual production path a bug here would break."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, lat=45.0, lon=9.0)
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "k")
    monkeypatch.setattr(CL, "_tomtom_stations",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("TomTom in sweep path")))
    monkeypatch.setattr(CL, "_query", lambda *a: [_node(1, 45.0, 9.0, operator="A2A")])
    assert CL.sweep_now() == 1
    assert _row(pdb, 1)["location_name"] == "A2A"    # OSM name, no TomTom touched


def test_relocate_button_never_uses_tomtom(monkeypatch):
    """Same rule for the manual 📍 recalc button (find_station_candidates): TomTom
    data can't be stored, and a charge's location_name IS stored — so even with a
    key configured, TomTom must never be queried on this path either."""
    monkeypatch.setattr(CL, "_tomtom_key", lambda: "k")
    monkeypatch.setattr(CL, "_tomtom_stations",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("TomTom in relocate path")))
    monkeypatch.setattr(CL, "_query", lambda *a: [_node(1, 45.0, 9.0, operator="A2A")])
    options, ok = CL.find_station_candidates(45.0, 9.0)
    assert ok is True
    assert [o["name"] for o in options] == ["A2A"]             # OSM name, no TomTom touched


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


def test_merge_prefers_richer_source_over_nearest(monkeypatch):
    """When two sources report the same physical station, the one carrying MORE
    fields (not the one a few metres closer) becomes the pin — including address/url,
    which a bare OSM tag set often lacks."""
    osm = [_node(1, 45.0002, 9.0, name="Enel X")]   # ~22 m: nearest, but no socket info
    monkeypatch.setattr(CL, "_query", lambda *a: osm)
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "Enel X", "lat": 45.0003, "lon": 9.0,
                                          "dist_m": 33, "info": "AC · 22 kW",
                                          "address": "Via Roma 1, Milano",
                                          "url": "https://openchargemap.org/poi/details/9"}])
    res = CL.find_nearby(45.0, 9.0, 2000)
    assert [s["name"] for s in res] == ["Enel X"]           # deduped to one pin
    assert res[0]["info"] == "AC · 22 kW"                    # richer OCM record won
    assert res[0]["address"] == "Via Roma 1, Milano"
    assert res[0]["url"] == "https://openchargemap.org/poi/details/9"


def test_merge_prefers_ocm_link_even_when_osm_is_the_richer_side(monkeypatch):
    """The link is the one field NOT decided by overall richness: Open Charge Map's
    own EV-detail page beats OpenStreetMap's bare tag view even on a station where
    OSM otherwise wins as the base (here, OSM alone has the address)."""
    osm = [_node(1, 45.0002, 9.0, name="Enel X",
                 **{"addr:street": "Via Roma", "addr:housenumber": "1", "addr:city": "Milano"})]
    monkeypatch.setattr(CL, "_query", lambda *a: osm)
    monkeypatch.setattr(CL, "_ocm_key", lambda: "k")
    monkeypatch.setattr(CL, "_ocm_stations",
                        lambda *a, **k: [{"name": "Enel X", "lat": 45.0003, "lon": 9.0,
                                          "dist_m": 33, "info": "", "address": None,
                                          "url": "https://openchargemap.org/poi/details/9"}])
    res = CL.find_nearby(45.0, 9.0, 2000)
    assert res[0]["address"] == "Via Roma 1, Milano"     # OSM's own field, richer side kept it
    assert res[0]["url"] == "https://openchargemap.org/poi/details/9"   # OCM's link still wins


def test_osm_station_carries_address_and_osm_url(monkeypatch):
    """A mapped addr:* tag set surfaces as `address`, and every OSM element gets a
    `url` back to its own OpenStreetMap page (node/way/relation + id)."""
    osm = [_node(7, 45.0, 9.0, name="Enel X",
                 **{"addr:street": "Via Roma", "addr:housenumber": "1", "addr:city": "Milano"})]
    monkeypatch.setattr(CL, "_query", lambda *a: osm)
    res = CL.find_nearby(45.0, 9.0, 2000)
    assert res[0]["address"] == "Via Roma 1, Milano"
    assert res[0]["url"] == "https://www.openstreetmap.org/node/7"


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
