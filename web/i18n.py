"""LeapMotor Mate — i18n (internationalisation).

Translations live in ``web/locales/{lang}.json`` (one file per language, shape
``{"translations": {key: string}, "months": {"full": [...12], "abbr": [...12]}}``).
This module loads them once at import time and exposes the SAME public API the rest of
the codebase already uses — nothing else changes:

    get_t(lang) -> t(key)          # translator; falls back to English, then the raw key
    fmt_month_year(lang, dt)       # "%B %Y"    → e.g. "Giugno 2026"
    fmt_day_month_year(lang, dt)   # "%d %b %Y" → e.g. "02 giu 2026"

The strings were extracted verbatim from the previous monolithic i18n.py (no wording
changed). Polish (pl) is the community translation by @irek (PR #90). Adding or editing a
translation now means editing a JSON file, not this module.
"""
import json
import os

_LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

# ── Load every locale once at import time ────────────────────────────────────
_T: dict[str, dict[str, str]] = {}
_MONTHS: dict[str, dict[str, list]] = {}
_WEEKDAYS: dict[str, list] = {}

for _fname in sorted(os.listdir(_LOCALES_DIR)):
    if not _fname.endswith(".json"):
        continue
    _lang = _fname[:-5]  # "en.json" → "en"
    with open(os.path.join(_LOCALES_DIR, _fname), encoding="utf-8") as _fh:
        _data = json.load(_fh)
    _T[_lang] = _data.get("translations", {})
    if "months" in _data:
        _MONTHS[_lang] = _data["months"]
    if "weekdays" in _data:
        _WEEKDAYS[_lang] = _data["weekdays"]["abbr"]


# ── Public API (identical behaviour to the old monolithic version) ───────────
def fmt_month_year(lang: str, dt) -> str:
    """Localized "%B %Y" → e.g. "Giugno 2026". Used for month headers in history trees."""
    months = _MONTHS.get(lang, _MONTHS["en"])
    return f"{months['full'][dt.month - 1]} {dt.year}"


def fmt_day_month_year(lang: str, dt) -> str:
    """Localized "%d %b %Y" → e.g. "02 giu 2026". Used for day labels in history trees."""
    months = _MONTHS.get(lang, _MONTHS["en"])
    return f"{dt.day:02d} {months['abbr'][dt.month - 1]} {dt.year}"


def weekday_abbrs(lang: str) -> list:
    """Monday-first 3-ish-letter weekday abbreviations for the Charges calendar's column
    header (e.g. ["Lun", "Mar", ...]) — a fixed 7-item list, unrelated to locale.strftime
    week-start conventions."""
    return _WEEKDAYS.get(lang, _WEEKDAYS["en"])


def get_t(lang: str):
    strings = _T.get(lang, _T["en"])
    fallback = _T["en"]

    def t(key: str) -> str:
        return strings.get(key, fallback.get(key, key))

    return t
