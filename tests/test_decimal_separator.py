"""One decimal separator for the whole app, not one per filter.

`money` and `price3` have always followed the UI language; nothing else did. In Italian the
Monthly Report printed a cost of "38,74 €" next to an energy of "110.3 kWh" and a price of
"0,250 €/kWh" — the same page writing the same kind of number three ways. `units.decimal_point`
now sits under both `units._num` (dist/speed/temp/pressure/elev/efficiency) and `main._nice`,
which between them format every displayed number.

The language is read for every single figure, so it is memoised — a settings row costs ~0.5 ms
and a busy page shows a couple of hundred numbers. The memo has to disappear the moment anything
is written, or switching language would leave the numbers behind.
"""
import db as D
import db_reader
import units


def _db(tmp_path, monkeypatch, lang):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader._lang_memo[0] = None
    db_reader.set_setting("language", lang)
    return pdb


def test_english_keeps_the_dot(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, "en")
    assert units.decimal_point("110.3") == "110.3"
    assert units.efficiency(16.8) == "16.8 kWh/100km"


def test_every_other_language_gets_the_comma(tmp_path, monkeypatch):
    for lang in ("it", "fr", "de", "nl", "pl", "pt-PT"):
        _db(tmp_path, monkeypatch, lang)
        assert units.decimal_point("110.3") == "110,3", lang
        assert units.efficiency(16.8) == "16,8 kWh/100km", lang


def test_a_whole_number_is_untouched(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, "it")
    assert units.decimal_point("655") == "655"
    assert units.dist(655.0, 0) == "655 km"


def test_switching_language_takes_effect_at_once(tmp_path, monkeypatch):
    # The memo must not outlive the setting: read Italian, switch to English, read again.
    _db(tmp_path, monkeypatch, "it")
    assert units.decimal_point("1.5") == "1,5"
    db_reader.set_setting("language", "en")
    assert units.decimal_point("1.5") == "1.5"
    db_reader.set_setting("language", "de")
    assert units.decimal_point("1.5") == "1,5"


def test_any_other_setting_write_also_clears_the_memo(tmp_path, monkeypatch):
    # set_setting drops it unconditionally rather than sniffing the key — one line, and it can
    # never be the thing that goes stale.
    _db(tmp_path, monkeypatch, "it")
    units.decimal_point("1.5")                       # prime the memo
    db_reader.set_setting("unit_system", "metric")
    assert db_reader._lang_memo[0] is None


def test_formatting_never_raises_without_a_database(monkeypatch):
    # A pure-conversion unit test, or a request that lands before the DB is reachable, must get a
    # number back rather than a traceback. The dot is the fallback.
    def boom():
        raise RuntimeError("no database")
    monkeypatch.setattr(db_reader, "get_language", boom)
    assert units.decimal_point("1.5") == "1.5"


def test_the_language_is_read_once_per_burst(tmp_path, monkeypatch):
    # 200 figures on a page must not be 200 settings queries.
    _db(tmp_path, monkeypatch, "it")
    db_reader._lang_memo[0] = None
    calls = []
    real = db_reader.get_setting

    def counted(key, default=""):
        if key == "language":
            calls.append(key)
        return real(key, default)

    monkeypatch.setattr(db_reader, "get_setting", counted)
    for _ in range(200):
        units.decimal_point("1.5")
    assert len(calls) == 1


# ── the trap: a comma inside a CSS width invalidates the rule ────────────────────
def test_no_number_filter_ever_lands_in_a_style_attribute():
    """`| nice` puts a comma in for six of the seven languages. Inside `style="width:{{ x }}%"`
    that is not a decimal separator, it is a syntax error — the bar silently collapses to zero
    width and nobody sees a stack trace. Bars, progress fills and SoC segments must stay dotted.

    This is not hypothetical: applying the filter across the templates hit eleven CSS widths on
    the first pass, including the three bars of the energy split."""
    import pathlib
    import re

    bad = []
    for p in pathlib.Path(__file__).resolve().parent.parent.joinpath("web/templates").rglob("*.html"):
        for m in re.finditer(r'style="[^"]*?\{\{[^{}]*?\|\s*(nice|eff|dist|speed|temp|pressure|elev)\b',
                             p.read_text(encoding="utf-8")):
            bad.append(f"{p.name}:{m.group(0)[:60]}")
    assert not bad, "a display filter reached a CSS value:\n" + "\n".join(bad)


def test_no_number_filter_ever_lands_inside_a_script_block():
    """Same trap one step further: a comma in a JS numeric literal is an argument separator."""
    import pathlib
    import re

    bad = []
    for p in pathlib.Path(__file__).resolve().parent.parent.joinpath("web/templates").rglob("*.html"):
        txt = p.read_text(encoding="utf-8")
        for a, b in [(m.start(), m.end()) for m in re.finditer(r"<script\b.*?</script>", txt, re.S)]:
            for m in re.finditer(r"\|\s*(nice|eff|dist|speed|temp|pressure|elev)\b", txt[a:b]):
                bad.append(f"{p.name}:{txt[:a + m.start()].count(chr(10)) + 1}")
    assert not bad, "a display filter reached JavaScript:\n" + "\n".join(bad)
