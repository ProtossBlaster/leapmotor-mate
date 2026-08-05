"""The bundle's `wallbox` field must be the TICK, not "Home Assistant answers" (#226).

It used to be `bool(ha_url or SUPERVISOR_TOKEN)`. Under the add-on `SUPERVISOR_TOKEN` is always
set, so that line printed `wallbox=True` to every add-on user alive — including @wlighter, who had
switched the feature off and said so. Triage read the field, believed the name, and told him he was
wrong in public. He was not: his case was the v3.8.0 defect, and the one fact that would have
settled it was the only one the bundle did not carry.

So: `wallbox` answers "is the feature on", `ha` answers "can we reach Home Assistant". Two
questions, two fields, and the add-on can no longer make one of them lie about the other.

The DB here is a real temp one — the setting has to travel the same road it travels in production
(`get_vehicle()` → settings dict), or the test would prove the key name is right by asserting it
twice.
"""
import pathlib

import db as PollerDB
import db_reader
import diagnostics as D


def _features(tmp_path, monkeypatch, *, tick=None, addon=False, ha_url=None):
    PollerDB.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    if tick is not None:
        db_reader.set_setting("wallbox_enabled", tick)
    if ha_url is not None:
        db_reader.set_setting("ha_url", ha_url)
    if addon:
        monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    else:
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    return D.build_system_info("9.9.9")["features"]


def test_the_tick_off_reads_off_even_as_an_addon(tmp_path, monkeypatch):
    """@wlighter's exact configuration: add-on, feature switched off. The old code said True."""
    f = _features(tmp_path, monkeypatch, tick="0", addon=True)
    assert f["wallbox"] is False, "an add-on user who unticked the wallbox is reported as having it on"


def test_the_tick_on_reads_on(tmp_path, monkeypatch):
    assert _features(tmp_path, monkeypatch, tick="1", addon=True)["wallbox"] is True


def test_the_tick_alone_decides_it_with_no_home_assistant_at_all(tmp_path, monkeypatch):
    """The switch is a stored choice; it does not read False because HA is unreachable — otherwise
    the field starts answering a second question again."""
    assert _features(tmp_path, monkeypatch, tick="1", addon=False, ha_url="")["wallbox"] is True


def test_never_set_reads_off(tmp_path, monkeypatch):
    """Same default as every other reader of this setting (`get_setting(..., "0")`)."""
    assert _features(tmp_path, monkeypatch, addon=True)["wallbox"] is False


def test_reachability_did_not_get_lost_it_moved_to_ha(tmp_path, monkeypatch):
    """What the old field really measured is still in the bundle, under a name that says so."""
    assert _features(tmp_path, monkeypatch, tick="0", addon=True)["ha"] is True
    assert _features(tmp_path, monkeypatch, tick="1", ha_url="http://ha.local")["ha"] is True
    assert _features(tmp_path, monkeypatch, tick="1", ha_url="")["ha"] is False


def test_both_facts_are_printed_on_the_features_line(tmp_path, monkeypatch):
    """A field nobody can read is not in the bundle: triage reads the text, not the dict."""
    _features(tmp_path, monkeypatch, tick="0", addon=True)
    line = [ln for ln in D.build_bundle("9.9.9", parts=("info",)).splitlines()
            if ln.startswith("Features")]
    assert line, "the Features line disappeared from the bundle"
    assert "wallbox=False" in line[0], line[0]
    assert "ha=True" in line[0], line[0]


def test_the_settings_page_shows_the_two_chips_apart():
    """The same dict feeds the Settings badge row — where it was labelled "Wallbox" and lit up green
    for anyone running the add-on."""
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "web" / "templates" / "settings.html").read_text()
    assert "('Wallbox', diag.features.wallbox)" in html
    assert "('HA', diag.features.ha)" in html


def test_the_reachability_expression_is_no_longer_named_wallbox():
    """This regression is one rename away: point `wallbox` back at the token and every add-on bundle
    lies again — silently, with the asserts above still green on a machine that has no
    SUPERVISOR_TOKEN."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "diagnostics.py").read_text()
    block = src.split('"features": {', 1)[1].split("},", 1)[0]
    wallbox = [ln for ln in block.splitlines() if '"wallbox"' in ln][0]
    assert "SUPERVISOR_TOKEN" not in wallbox, wallbox
    assert "wallbox_enabled" in wallbox, wallbox
