"""The range-extender pages ship on the official build: `is_reev` decides, `MATE_RESEARCH` does not.

REEV support was built behind two gates at once — the car's capability (`is_reev`) and the
BetaTester build (`MATE_RESEARCH`) — because the fuel figures had never been checked against a
number the owner could see. On 29/09/2026 they were: @ebagnoli's bundle carries one drive for which
the official app states 77 km, 0.3 kWh and 4.9 L, and the cloud's own per-trip record
(`driveReevOil`) reads 77.0 / 0.3 / 4.9 — the same three figures. That closed the calibration, so
the second gate has no job left.

What this file holds is the LINE between the two kinds of gate, because the difference is the whole
point and a single missed copy puts it back:

* **capability** — a plain electric car must never reach a fuel surface, on any build. Unchanged.
* **build** — the research MACHINERY (signal capture, the logbook, the encrypted bundle, the consent
  page, the layout preview) stays BetaTester-only, and so does the one unvalidated figure that is
  deliberately absent from the official build (`reev_elec_kwh_100km`, @michapr beta #27).

🔑 The structural test is the one that matters. Fourteen templates carried `is_reev and research`;
ungating thirteen and forgetting one would leave a REEV owner with a page that half works, and no
test of a single feature would notice. So the ALLOW-LIST is asserted, not the count.
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# The ONE place a REEV surface may still ask for the beta build: the electric rate on a generator
# trip, which the poller withholds as a ΔSoC figure and which getEC cannot measure either (the
# generator feeds the wheels without passing through the pack). It is on the beta build to be held
# against the car's own dashboard, and nowhere else. See trip_detail.html's own note.
RESEARCH_ONLY_TEMPLATE_GATES = {
    "templates/trip_detail.html": [
        "{% elif is_reev and research and trip.engine_ran"
        " and trip.reev_elec_kwh_100km is not none %}",
    ],
}


def _template_gate_sites():
    """Every template line that gates a REEV surface on the beta build, as {relative path: [lines]}."""
    found = {}
    for path in sorted(WEB.rglob("*.html")):
        hits = [line.strip() for line in path.read_text().splitlines()
                if "is_reev and research" in line]
        if hits:
            found[path.relative_to(WEB).as_posix()] = hits
    return found


def test_no_range_extender_surface_is_coupled_to_the_beta_build_except_the_unvalidated_rate():
    """The allow-list, asserted whole: anything else still on `research` is a feature a REEV owner
    paid for and cannot see."""
    assert _template_gate_sites() == RESEARCH_ONLY_TEMPLATE_GATES


def test_the_beta_only_electric_rate_is_still_beta_only():
    """The mirror of the test above — it must not pass by having ungated everything."""
    body = (WEB / "templates" / "trip_detail.html").read_text()
    assert "is_reev and research and trip.engine_ran and trip.reev_elec_kwh_100km" in body, \
        "the unvalidated electric rate escaped onto the official build"


def test_the_generator_figures_are_computed_on_the_official_build():
    """`reev_fuel`, `reev_breakeven` and the fuel band of the efficiency chart are the three
    figures the Overview computes. Gated on the DATA, never on the markup — so the gate has to go
    from the computation, not from a template."""
    body = (WEB / "main.py").read_text()
    for needle in ("reev_fuel_summary()", "reev_breakeven_kwh_price()", "include_fuel="):
        lines = [l for l in body.splitlines() if needle in l]
        assert lines, f"{needle} is gone from the Overview entirely"
        for line in lines:
            assert "research" not in line, \
                f"{needle} still waits for the beta build: {line.strip()}"


def test_the_wizard_offers_the_range_extender_packs_on_every_build():
    """The gate I missed on the first pass, and the one that would have bitten hardest: the battery
    list used to drop every pack marked `reev` unless MATE_RESEARCH was on. Shipping the REEV pages
    while hiding the pack leaves an owner unable to finish the wizard at all — a worse silence than
    the one that filter was written to prevent."""
    import main
    every = {o["label"] for opts in main._EU_BATTERY_MAP.values() for o in opts if o.get("reev")}
    assert every, "there are no REEV packs in the map at all — this test measures nothing"
    offered = {o["label"] for opts in main.battery_options_for_build().values()
               for o in opts if o.get("reev")}
    assert offered == every, f"the official wizard still hides {sorted(every - offered)}"


def test_only_the_research_machinery_still_asks_for_the_beta_build():
    """The whole allow-list of `research_enabled()` calls in web/main.py, by the line they sit on —
    so a sixteenth gate on a REEV surface fails here instead of on an owner's screen. Everything
    listed is either the research machinery itself or the `research` flag handed to a template,
    which still needs it for the beta badge and the one unvalidated figure."""
    import re
    body = (WEB / "main.py").read_text()
    lines = [l.strip() for l in body.splitlines() if "research.research_enabled()" in l]
    allowed = (
        '"research": research.research_enabled(),',              # handed to a template
        '"is_reev": db_reader.is_reev_car(), "research": research.research_enabled(),',
        'if research.research_enabled() and db_reader.get_setting("research_consent", "0") != "1" \\',
        'is_research = research.research_enabled()',              # the REEV page's own tools + ?demo
        'if not research.research_enabled():',                    # logbook, export, consent
    )
    unexpected = [l for l in lines if not any(re.fullmatch(re.escape(a), l) for a in allowed)]
    assert not unexpected, f"a new beta gate appeared: {unexpected}"


def test_the_fuel_page_is_gated_on_the_car_and_not_on_the_build(tmp_path, monkeypatch):
    """`_fuel_blocked` guards the page AND its nine write endpoints, so it is the single gate that
    decides whether a REEV owner can log a refuel at all."""
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    pytest.importorskip("fastapi")
    import db as D
    import db_reader
    import main

    path = str(tmp_path / "t.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.delenv("MATE_RESEARCH", raising=False)

    db_reader.set_setting("is_reev", "1")
    assert main._fuel_blocked() is False, \
        "a range-extender on the official build still cannot reach its own fuel page"

    db_reader.set_setting("is_reev", "0")
    assert main._fuel_blocked() is True, \
        "a plain electric car reached a fuel surface — the capability gate is gone"


def _reev_page_source() -> str:
    """The body of `reev_page`, from its decorator to the next route — sliced so the assertions below
    cannot read a gate that belongs to some other page."""
    body = (WEB / "main.py").read_text()
    start = body.index('@app.get("/reev"')
    end = body.index("@app.get(", start + 10)
    chunk = body[start:end]
    assert "RedirectResponse" in chunk, "the REEV page lost its redirect entirely"
    return chunk


def test_the_range_extender_page_still_turns_away_a_plain_electric_car():
    """The capability half of the gate survives the ungating, and the `?demo` layout preview stays on
    the beta build (it invents signals for a car that has none)."""
    chunk = _reev_page_source()
    gate = next(l for l in chunk.splitlines() if l.strip().startswith("if not")
                and "is_reev_car" in l)
    assert "is_research" not in gate, f"the REEV page still asks for the beta build: {gate.strip()}"
    demo = next(l for l in chunk.splitlines() if "demo = bool(" in l)
    assert "is_research" in demo, "the layout preview escaped the beta build"
