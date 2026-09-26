"""Explicit local-time contract. Reject DST gaps and ambiguous wall times."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .errors import ValidationError


def local_start(value, timezone_name):
    if not isinstance(timezone_name,str) or not timezone_name:
        raise ValidationError('Explicit IANA vehicle timezone required')
    try:
        zone=ZoneInfo(timezone_name)
        naive=datetime.strptime(value,'%Y-%m-%d %H:%M:%S')
        candidates=[]
        for fold in (0,1):
            candidate=naive.replace(tzinfo=zone,fold=fold)
            if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)==naive:
                candidates.append(candidate)
        if not candidates or len({c.utcoffset() for c in candidates})!=1:
            raise ValueError()
        return candidates[0]
    except (ValueError,TypeError,ZoneInfoNotFoundError):
        raise ValidationError('Invalid, nonexistent or ambiguous local appointment time') from None
