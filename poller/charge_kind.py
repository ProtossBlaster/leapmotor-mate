"""Whether a charge is DC: one rule for the poller that records a charge's type and the pages that read it.

Shared by the poller and the web, so it imports nothing: the web loads it from poller/ for the length
of one import, since the two directories share module names."""

DC_MIN_KW_DEFAULT = 11.0


def dc_min_kw(setting) -> float:
    """The peak above which a charge is DC: the Advanced setting `charge_dc_min_kw`, else 11 kW."""
    try:
        return float(setting or DC_MIN_KW_DEFAULT)
    except (TypeError, ValueError):
        return DC_MIN_KW_DEFAULT


def is_dc(charge_type, max_power_kw, min_kw: float) -> bool:
    """Its recorded type; with none recorded, a peak above `min_kw`."""
    return charge_type == "DC" or (charge_type is None and (max_power_kw or 0) > min_kw)
