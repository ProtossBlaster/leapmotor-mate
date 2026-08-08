"""Bulk charge import from a user-filled CSV (idea from #111). Pure — no DB, no HTTP — so the parsing
and the STRICT validation are fully unit-testable. `parse_charge_csv` returns (rows, errors): `rows` are
clean dicts ready for db_reader.add_manual_charge, `errors` are human-readable per-line problems. The
import endpoint inserts ONLY `rows`, so a single bad line never blocks the whole file AND never lands
dirty data in the DB — every rejected line is reported back with the reason. Excel round-trips CSV
natively, so the same file works whether the user edits it in Excel, Numbers or a text editor."""
from datetime import date as _date
from datetime import datetime, timezone
import csv
import io

# The template columns, in order. `date` + `energy_kwh` are required; the rest are optional. New optional
# columns go at the END so existing files/positions never shift.
COLUMNS = ("date", "energy_kwh", "cost", "type", "start_soc", "end_soc", "end", "odometer_km")

# Every header spelling we accept per column. The template hands out the left-hand names; the right-hand
# ones are what Mate's OWN charges export writes (#182). That export is a raw DB dump whose first column
# is `id`, so before this map the importer read the header as data and rejected the file on its very
# first cell — "bad date 'id'" — and then on each row's id: 4, 3, 2, 1. Mapping by NAME instead of by
# position closes the export → import round trip without taking a single column away from the export,
# which people also open in a spreadsheet. Unknown columns (id, vehicle_id, latitude, …) are ignored.
_ALIASES = {
    "date":       {"date", "data", "datum", "date_time", "datetime", "started_at"},
    "energy_kwh": {"energy_kwh", "energy", "kwh", "energy_added_kwh"},
    "cost":       {"cost", "price"},
    "type":       {"type", "charge_type"},
    "start_soc":  {"start_soc"},
    "end_soc":    {"end_soc"},
    "end":        {"end", "ended_at"},
    # #237 — the car's odometer when the charge started. The right-hand spellings are what people
    # actually write in a spreadsheet they have been keeping by hand for months.
    "odometer_km": {"odometer_km", "odometer", "odo", "km", "mileage", "kilometraggio"},
}
_HEADER_ALIASES = _ALIASES["date"]     # first-cell values that mean "header row"
MAX_KWH = 250.0            # a single session above this is almost certainly a typo (biggest pack here ~100 kWh)
# An odometer above this is a typo, not a car — the same role MAX_KWH plays, and set well clear of
# any real vehicle so it never rejects somebody's genuinely enormous mileage.
MAX_ODO_KM = 3_000_000.0


def _header_map(cells):
    """{canonical column → index} when this row is a header, else None. A row counts as a header only
    when both REQUIRED columns name themselves in it, which no data row can do."""
    idx = {}
    for i, c in enumerate(cells):
        k = c.strip().lower()
        for canon, names in _ALIASES.items():
            if k in names and canon not in idx:
                idx[canon] = i
    return idx if ("date" in idx and "energy_kwh" in idx) else None

# Empty template we hand the user — self-documenting, with commented examples they delete. The importer
# skips every line starting with '#', so the instructions and the sample rows are never imported.
TEMPLATE = (
    "# LeapMotor Mate - charge import template\n"
    "# One charge per row. Lines starting with '#' are IGNORED (delete the two example rows below).\n"
    "#\n"
    "# date       (required) : YYYY-MM-DD  or  YYYY-MM-DD HH:MM   - not in the future\n"
    "# energy_kwh (required) : kWh added, e.g. 42.5              - 0 to 250, dot or comma decimal\n"
    "# cost       (optional) : amount paid, e.g. 8.10            - leave blank if unknown\n"
    "# type       (optional) : AC or DC                          - leave blank to default to AC\n"
    "# start_soc  (optional) : battery % at start, e.g. 23       - 0-100, blank if unknown\n"
    "# end_soc    (optional) : battery % at end, e.g. 80         - 0-100, blank if unknown\n"
    "# end        (optional) : end date/time, YYYY-MM-DD HH:MM   - for the duration; blank = no duration\n"
    "# odometer_km(optional) : the car's total KM at that charge  - always km, never miles\n"
    "#                         Fill this in and Mate can work out the cost per 100 km of charges\n"
    "#                         made before it was installed - it has no other way to know them.\n"
    "#\n"
    "# Re-importing a charge Mate already has does NOT create a second one: same date and same\n"
    "# energy_kwh means the same session, and the row is filled in rather than added again.\n"
    "#\n"
    "# Example (delete these two lines before importing):\n"
    "# 2025-11-03 21:30,42.5,8.10,AC,23,80,2025-11-04 01:37,18450\n"
    "# 2026-01-15,18,9.5,DC,,,,19102\n"
    "date,energy_kwh,cost,type,start_soc,end_soc,end,odometer_km\n"
)

_DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_dt(s: str):
    """Naive when the text carries no zone (what a person types), AWARE when it does — Mate's own
    export writes a full ISO stamp with the offset, and that offset is the truth, not a guess."""
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _to_utc(dt: datetime, tz) -> datetime:
    """A time the user typed is a time on THEIR clock. Store it as UTC, because every other timestamp
    in the DB is UTC and the renderer assumes a zone-less value is already UTC (#181: without this the
    display added the offset a second time — +7 h for the reporter, on all 150 of his charges)."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(timezone.utc)


def _num(s: str) -> float:
    # accept both "42.5" and the European "42,5" (in a ';'-delimited file the comma is the decimal)
    return float(str(s).strip().replace(",", "."))


def _opt_soc(s: str):
    """Parse an optional SoC %: blank → None, else a 0-100 number (raises ValueError otherwise)."""
    s = (s or "").strip()
    if not s:
        return None
    v = _num(s)                              # raises ValueError if not a number
    if not (0.0 <= v <= 100.0):
        raise ValueError("SoC out of 0-100")
    return v


def _sniff_delimiter(text: str) -> str:
    """European Excel (IT/FR/DE locales) saves CSV with ';' as the field separator and ',' as the
    decimal — US/UK Excel uses ',' and '.'. Pick the delimiter from the first real line so both a
    `date,energy` and a `date;energy` file import correctly (and `30,5` stays one number, not two)."""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        return ";" if s.count(";") > s.count(",") else ","
    return ","


def parse_charge_csv(text: str, *, tz, today=None):
    """Parse + validate the CSV text. Returns (rows, errors).

    tz    : the zone the times in the file are written in — REQUIRED, no default. A charge CSV cannot
            be read without knowing whose clock its times are on; guessing that once cost every
            imported charge a whole offset (#181). `started_at`/`ended_at` come back as UTC.
    rows  : list of {started_at, energy_kwh, cost, charge_type} dicts (feed each to add_manual_charge).
    errors: list of "line N: <reason>" strings for every rejected row (header/blank/# lines are silent).
    Strict on purpose — a charge that can't be trusted (unparseable date, future date, non-positive or
    absurd energy, negative cost, unknown type) is rejected, not guessed."""
    today = today or _date.today()
    rows: list[dict] = []
    errors: list[str] = []
    seen_header = False
    colmap = None                  # set from the header row; None → read the template's fixed order
    delim = _sniff_delimiter(text)

    def cell(cells, canon):
        """One column's raw text, by name when a header told us where it is, else by position."""
        i = colmap.get(canon) if colmap else (COLUMNS.index(canon) if canon in COLUMNS else None)
        return cells[i] if (i is not None and i < len(cells)) else ""

    for i, raw in enumerate(csv.reader(io.StringIO(text), delimiter=delim), start=1):
        if not raw or all(not c.strip() for c in raw):
            continue                                             # blank line
        if raw[0].lstrip().startswith("#"):
            continue                                             # comment / instructions
        cells = [c.strip() for c in raw]
        if not seen_header:
            hmap = _header_map(cells)
            if hmap or cells[0].lower() in _HEADER_ALIASES:
                seen_header = True
                colmap = hmap                                    # None → keep the positional order
                continue                                         # the column-name header row
        date_s, energy_s = cell(cells, "date"), cell(cells, "energy_kwh")
        if not date_s or not energy_s:
            errors.append(f"line {i}: needs at least a date and energy_kwh")
            continue

        dt = _parse_dt(date_s)
        if dt is None:
            errors.append(f"line {i}: bad date '{date_s}' — use YYYY-MM-DD or YYYY-MM-DD HH:MM")
            continue
        if dt.date() > today:
            errors.append(f"line {i}: date '{date_s}' is in the future")
            continue

        try:
            energy = _num(energy_s)
        except ValueError:
            errors.append(f"line {i}: energy_kwh '{energy_s}' is not a number")
            continue
        if not (0 < energy <= MAX_KWH):
            errors.append(f"line {i}: energy_kwh must be greater than 0 and at most {MAX_KWH:.0f}")
            continue

        cost = None
        cost_s = cell(cells, "cost")
        if cost_s:
            try:
                cost = _num(cost_s)
            except ValueError:
                errors.append(f"line {i}: cost '{cost_s}' is not a number")
                continue
            if cost < 0:
                errors.append(f"line {i}: cost cannot be negative")
                continue

        type_s = cell(cells, "type").upper()
        ctype = "DC" if type_s in ("DC", "FAST", "HPC") else ("AC" if type_s in ("", "AC") else None)
        if ctype is None:
            errors.append(f"line {i}: type '{type_s}' must be AC or DC")
            continue

        # Optional start/end SoC % (0-100) → the card's SoC-gain tile (#67 @rossiadobe). Imported charges
        # have no power curve, so they stay excluded from the SoH estimate whether or not SoC is given.
        try:
            start_soc = _opt_soc(cell(cells, "start_soc"))
        except ValueError:
            errors.append(f"line {i}: start_soc '{cell(cells, 'start_soc')}' must be a number 0-100")
            continue
        try:
            end_soc = _opt_soc(cell(cells, "end_soc"))
        except ValueError:
            errors.append(f"line {i}: end_soc '{cell(cells, 'end_soc')}' must be a number 0-100")
            continue

        # Noon default when no time given → the charge never day-shifts on display across time zones,
        # matching the manual-entry form's own 12:00 default.
        start_final = dt if (dt.hour or dt.minute) else dt.replace(hour=12)

        # Optional end date/time → the charge's duration (#67 @rossiadobe). Full 'YYYY-MM-DD HH:MM' (or
        # date); must not be before the start. Blank → None (add_manual_charge then makes end == start).
        # Compare in UTC: a file can mix a zoned start with a bare end (our export carries the offset,
        # a hand-typed line doesn't), and Python refuses to order an aware datetime against a naive one.
        start_utc = _to_utc(start_final, tz)
        ended_at = None
        end_s = cell(cells, "end")
        if end_s:
            end_dt = _parse_dt(end_s)
            if end_dt is None:
                errors.append(f"line {i}: end '{end_s}' — use YYYY-MM-DD HH:MM")
                continue
            end_utc = _to_utc(end_dt, tz)
            if end_utc < start_utc:
                errors.append(f"line {i}: end '{end_s}' is before the start")
                continue
            ended_at = end_utc.isoformat()

        # Optional odometer (#237). This is the only route by which a charge from BEFORE Mate was
        # installed can carry kilometres at all: no poll of it was ever made, so nothing in the
        # database can supply them. Zero is refused rather than stored — an odometer reading of 0
        # would place the session at the factory gate, which is a wrong number, not a missing one.
        odo = None
        odo_s = cell(cells, "odometer_km")
        if odo_s:
            try:
                odo = _num(odo_s)
            except ValueError:
                errors.append(f"line {i}: odometer_km '{odo_s}' is not a number")
                continue
            if not (0 < odo <= MAX_ODO_KM):
                errors.append(f"line {i}: odometer_km must be greater than 0 "
                              f"and at most {MAX_ODO_KM:.0f}")
                continue

        rows.append({
            "started_at": start_utc.isoformat(),
            "ended_at": ended_at,
            "energy_kwh": round(energy, 3),
            "cost": round(cost, 2) if cost is not None else None,
            "charge_type": ctype,
            "start_soc": round(start_soc, 1) if start_soc is not None else None,
            "end_soc": round(end_soc, 1) if end_soc is not None else None,
            "odometer_km": round(odo, 1) if odo is not None else None,
        })
    return rows, errors
