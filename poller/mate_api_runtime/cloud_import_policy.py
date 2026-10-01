"""Read-only preference resolution; disabling never deletes imported history."""
KEY = 'api_v2_import_cloud_trips'

def trips_enabled(db):
    row = db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
    if row is not None:
        return row[0] == '1'
    # Preserve the behavior of installations already using cloud import.
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_lab_cloud_trip_links'").fetchone()
    return bool(exists and db.execute('SELECT 1 FROM api_lab_cloud_trip_links LIMIT 1').fetchone())

def import_trips(db, importer):
    return importer(db) if trips_enabled(db) else {'state': 'disabled'}

FROM_KEY = 'api_v2_import_cloud_from'
# The first month offered, and the first one asked. Measured 01/10/2026 with the worker's own
# client: the cloud returned no single trip before 1 September 2026 00:00 (June, July and August
# empty, Mate's own 449 trips in them notwithstanding). Silvio: from September, and not before.
FIRST_MONTH = (2026, 9)

def past_months(today):
    """The months before `today`'s that can be imported, oldest first, none before FIRST_MONTH."""
    months, (year, month) = [], FIRST_MONTH
    while (year, month) < (today.year, today.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months

def import_from(db):
    """The month the earlier months are imported from, or None for the current month only.

    A month before FIRST_MONTH, which only a hand-edited setting can hold, starts at FIRST_MONTH."""
    row = db.execute('SELECT value FROM settings WHERE key=?', (FROM_KEY,)).fetchone()
    try:
        year, month = (int(part) for part in row[0].split('-'))
    except (TypeError, ValueError, AttributeError):
        return None
    return max((year, month), FIRST_MONTH) if 1 <= month <= 12 else None
