"""The wizard's manual-entry hint must quote the packs Mate actually uses, in every language.

The field it sits under decides every kWh, €/kWh and consumption figure that install will ever
print, and it wants the **usable (net)** capacity — the number `battery_packs.EU_BATTERY_MAP`
offers when the model is recognised. The hint under it listed something else:

  * `B10 Pro: 56.2 · B10 Pro Max: 67.1 · T03: 37.3` are the **gross** nameplate figures, while
    `C10 RWD: 67.0 · C10 AWD: 81.9` on the same line are net — two units, one list;
  * two of the eight languages still read `C10 RWD: 69,9`, the value #246 disproved and v3.11.2
    replaced;
  * `B05` was missing from all eight, although the wizard offers it a pack;
  * the input was pre-filled with **67.1** — the gross B10 Pro Max — so anyone whose car was not
    recognised, which is the ONLY way to reach this field, was one click away from committing a
    stranger's battery. On a T03 that is an 86% error, applied in silence.

This file is the reason it cannot drift again: every number in the hint has to exist in the map.
It deliberately does NOT import web.main (fastapi is absent in the minimal CI env, and
`test_reev_variant_setup.py` skips itself there, leaving the pack figures unprotected) — the map
lives in its own module for exactly that reason.
"""
import pathlib
import re

import pytest

import battery_packs

SETUP = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" / "setup.html"
LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es")


def _hints():
    """{language-block index: the manualHint line}. Parsed by position, not by language key: the
    blocks are keyed differently ('pt-PT' is quoted, en is not) and what matters here is that
    EVERY block is checked, whatever it is called."""
    return re.findall(r'manualHint:\s*"([^"]+)"', SETUP.read_text())


def _numbers(s):
    return {n.replace(",", ".") for n in re.findall(r"\d+[.,]\d+", s)}


ALL_PACKS = {o["v"] for opts in battery_packs.EU_BATTERY_MAP.values() for o in opts}


def test_the_hint_exists_in_every_language():
    assert len(_hints()) == len(LANGS), f"{len(_hints())} hints for {len(LANGS)} languages"


@pytest.mark.parametrize("i", range(len(LANGS)))
def test_every_figure_in_the_hint_is_a_pack_mate_offers(i):
    extra = _numbers(_hints()[i]) - ALL_PACKS
    assert not extra, f"language block #{i} quotes {sorted(extra)}, which Mate never uses"


@pytest.mark.parametrize("i", range(len(LANGS)))
def test_the_hint_covers_every_model(i):
    """A model missing from the list reads as "Mate has no figure for mine" to the one person who
    got here — the owner of a car the cloud did not recognise."""
    hint = _hints()[i]
    for model in ("B10", "C10", "T03", "B05"):
        assert model in hint, f"language block #{i} never mentions the {model}"


def test_the_field_is_not_pre_filled_with_somebody_elses_battery():
    """Empty, so the wizard's own validation asks for a number. A default here is not a
    convenience: it is a wrong pack that nobody chose and nobody sees again."""
    src = SETUP.read_text()
    assert 'id="manual-battery"' in src
    field = src[src.index('id="manual-battery"'):][:400]
    assert 'value="67.1"' not in field, "the gross B10 Pro Max is still pre-filled"
    assert "h-battery').value = '67.1'" not in src, "a failure path still writes 67.1"


# ── the numbers themselves, protected without fastapi ─────────────────────────
def test_the_packs_are_the_usable_figures_measured_or_published():
    """#246 settled the C10 RWD on real charges: at 69.9 the battery took 100.8% of what the
    charger delivered. The rest are the published usable figures. Asserted here because the file
    that used to hold them skips itself in CI."""
    got = {ct: {o["v"] for o in opts} for ct, opts in battery_packs.EU_BATTERY_MAP.items()}
    assert got["C10"] == {"67.0", "81.9", "28.4"}
    assert got["B10"] == {"55.0", "65.0", "18.8"}
    assert got["B05"] == {"55.0", "65.0"}
    assert got["T03"] == {"36.0"}


def test_the_reev_packs_are_marked_as_such():
    """The flag is what keeps them out of the official build's wizard (#141)."""
    reev = {o["v"] for opts in battery_packs.EU_BATTERY_MAP.values() for o in opts if o.get("reev")}
    assert reev == {"28.4", "18.8"}
