"""The car's charge window on the Overview (#173 @rop12770).

He sent the official app's own banner — a clock and "22:05 - 07:55 do dia seguinte" over a parked,
plugged-in car. Mate has had the window for a long time, but only inside its Scheduling page, so the
one moment it answers a question — cable in, nothing happening, why? — it wasn't there.

The window lives in the CAR, not in the poll frame, so reading it costs a cloud round-trip while the
Overview redraws every 30 s. The poller therefore caches it into settings and the web only ever reads
those; everything below is about that cached value being turned into a string, or refused.
"""
import main


def _settings(monkeypatch, **kv):
    monkeypatch.setattr(main.db_reader, "get_setting", lambda k, d="": kv.get(k, d))


def test_window_is_rendered_when_enabled(monkeypatch):
    _settings(monkeypatch, charge_sched_enabled="1",
              charge_sched_start="22:05", charge_sched_end="07:55")
    assert main._charge_window() == "22:05 – 07:55"


def test_no_window_when_the_schedule_is_off(monkeypatch):
    _settings(monkeypatch, charge_sched_enabled="0",
              charge_sched_start="22:05", charge_sched_end="07:55")
    assert main._charge_window() == ""


def test_identical_times_mean_nothing_is_set(monkeypatch):
    # 00:00–00:00 is how the cloud says "no window". Showing it would be worse than showing nothing.
    _settings(monkeypatch, charge_sched_enabled="1",
              charge_sched_start="00:00", charge_sched_end="00:00")
    assert main._charge_window() == ""


def test_a_missing_half_is_not_half_a_window(monkeypatch):
    _settings(monkeypatch, charge_sched_enabled="1", charge_sched_start="22:05", charge_sched_end="")
    assert main._charge_window() == ""
    _settings(monkeypatch, charge_sched_enabled="1", charge_sched_start="", charge_sched_end="07:55")
    assert main._charge_window() == ""


def test_nothing_cached_yet_is_silent(monkeypatch):
    # A fresh install, or a car whose schedule has never been read: no chip, no error.
    _settings(monkeypatch)
    assert main._charge_window() == ""


def test_a_broken_settings_read_cannot_take_the_page_down(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(main.db_reader, "get_setting", boom)
    assert main._charge_window() == ""


def test_every_language_has_the_label():
    """Six locales, and the placeholder has to survive translation — a language that dropped
    {window} would render the chip with no time in it, which is the whole point of the chip."""
    import json
    import pathlib
    for p in sorted(pathlib.Path("web/locales").glob("*.json")):
        d = json.loads(p.read_text())["translations"]
        assert "hero_charge_window" in d, p.name
        assert "{window}" in d["hero_charge_window"], p.name
