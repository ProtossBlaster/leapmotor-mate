"""A charge imported at exactly 00:00 was moved to noon (30/08 audit).

The import gives a bare DATE the noon default, so a charge with no time cannot day-shift across
time zones — the same 12:00 the manual-entry form uses. But it decides that from the parsed value:

    start_final = dt if (dt.hour or dt.minute) else dt.replace(hour=12)

and midnight is `0 or 0`. So a row that DOES carry a time, written `2026-08-15 00:00`, is read as
"no time given" and shifted twelve hours — into a different time-of-use band, which is a different
price, and out of round-trip with Mate's own export.

Same family as the assent-versus-zero rule: the question is whether a time was WRITTEN, and that
cannot be answered by looking at the number it parsed to. Only `00:00` is affected — `00:05` has a
truthy minute — which is why it survived: it is the one midnight nobody tests.

CI-safe: pure import parsing, no db, no fastapi.
"""
import charge_import


def test_a_written_midnight_is_kept():
    dt = charge_import._parse_dt("2026-08-15 00:00")
    assert dt is not None and (dt.hour, dt.minute) == (0, 0)
    assert charge_import._start_of("2026-08-15 00:00", dt).hour == 0, \
        "00:00 was written down — it is a time, not a missing one"


def test_a_bare_date_still_becomes_noon():
    """The noon default exists so a dateless charge cannot day-shift across zones. It stays."""
    dt = charge_import._parse_dt("2026-08-15")
    assert charge_import._start_of("2026-08-15", dt).hour == 12


def test_an_ordinary_time_is_untouched():
    dt = charge_import._parse_dt("2026-08-15 07:30")
    out = charge_import._start_of("2026-08-15 07:30", dt)
    assert (out.hour, out.minute) == (7, 30)
