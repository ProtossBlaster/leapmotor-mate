"""Bounded calendar history reads, preserving source records and incompleteness."""
import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .cloud import ProtocolError, freeze, validate_dates
from .errors import ValidationError


@dataclass(frozen=True, slots=True)
class HistoryResult:
    records: tuple = field(repr=False)
    complete: bool
    reason: str
    source: str = field(default="cloud", init=False)


def calendar_window(start, end, timezone):
    validate_dates(start, end)
    if not isinstance(timezone, str) or not timezone or len(timezone) > 128:
        raise ValidationError("Explicit IANA timezone required")
    try:
        zone = ZoneInfo(timezone)
        first = int(datetime.combine(start, time.min, zone).timestamp())
        last = int(datetime.combine(end + timedelta(days=1), time.min, zone).timestamp()) - 1
    except (ZoneInfoNotFoundError, ValueError, OverflowError, OSError):
        raise ValidationError("Invalid calendar window") from None
    if last < first:
        raise ValidationError("Empty calendar window")
    return first, last


def list_history(client, vin, start, end, *, timezone, max_pages, kind):
    first, last = calendar_window(start, end, timezone)
    if type(max_pages) is not int or not 1 <= max_pages <= 100 or kind not in ("charge", "mileage"):
        raise ValidationError("Invalid history bounds")
    context = client._history_context(vin)
    records, seen_records, seen_pages = [], set(), set()
    baseline = None

    def result(complete, reason):
        return HistoryResult(tuple(freeze(row) for row in records), complete, reason)

    for number in range(1, max_pages + 1):
        data = client._history_page(context, kind, first, last, number)
        for key in ("pageNum", "pageSize", "totalPage", "total"):
            if type(data.get(key)) is not int or data[key] < 0:
                raise ProtocolError("invalid_page_metadata")
        if data["pageNum"] != number or data["pageSize"] != 20:
            raise ProtocolError("unexpected_page_metadata")
        rows = data.get("list")
        if not isinstance(rows, list) or len(rows) > 20 or any(not isinstance(row, dict) for row in rows):
            raise ProtocolError("invalid_record_list")
        fingerprints = tuple(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows)
        repeated = bool(rows) and fingerprints in seen_pages
        duplicate = len(set(fingerprints)) != len(fingerprints) or bool(seen_records.intersection(fingerprints))
        records.extend(rows)
        seen_pages.add(fingerprints)
        seen_records.update(fingerprints)
        total, pages = data["total"], data["totalPage"]
        if repeated:
            return result(False, "repeated_page")
        if baseline is not None and baseline != (total, pages):
            return result(False, "totals_changed")
        baseline = (total, pages)
        if duplicate:
            return result(False, "duplicate_records")
        if total == 0:
            return result(not records and pages in (0, 1), "empty" if not records and pages in (0, 1) else "inconsistent_totals")
        if pages != (total + 19) // 20:
            return result(False, "inconsistent_totals")
        if len(records) > total:
            return result(False, "record_count_mismatch")
        if not rows and len(records) < total:
            return result(False, "empty_page_before_total")
        if number >= pages:
            return result(len(records) == total, "complete" if len(records) == total else "record_count_mismatch")
    return result(False, "page_limit")
