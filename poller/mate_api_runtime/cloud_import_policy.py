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
