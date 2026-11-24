"""S7-owned durable SQLite storage; no station/price data is fabricated."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from threading import RLock

from app.models import to_iso, utc_now


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS stations (
    station_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
    available_fuel_types TEXT NOT NULL,
    catalog_source TEXT NOT NULL,
    catalog_updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stations_lat_lon ON stations(latitude, longitude);
CREATE TABLE IF NOT EXISTS price_reports (
    price_report_id TEXT PRIMARY KEY NOT NULL,
    station_id TEXT NOT NULL REFERENCES stations(station_id),
    fuel_type TEXT NOT NULL,
    price_millis INTEGER NOT NULL CHECK(price_millis BETWEEN 1 AND 20000),
    currency TEXT NOT NULL CHECK(currency = 'CAD'),
    observed_at TEXT,
    submitted_at TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK(verification_status IN ('unverified','reviewed')),
    reporter_subject TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reports_station_fuel_time
    ON price_reports(station_id, fuel_type, submitted_at DESC, price_report_id DESC);
CREATE TABLE IF NOT EXISTS current_prices (
    station_id TEXT NOT NULL REFERENCES stations(station_id),
    fuel_type TEXT NOT NULL,
    price_report_id TEXT NOT NULL REFERENCES price_reports(price_report_id),
    PRIMARY KEY (station_id, fuel_type)
);
CREATE TABLE IF NOT EXISTS submission_idempotency (
    subject TEXT NOT NULL,
    station_id TEXT NOT NULL,
    idem_key TEXT NOT NULL,
    body_fingerprint TEXT NOT NULL,
    response_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (subject, station_id, idem_key)
);
"""


class IdempotencyConflict(Exception):
    pass


class UnknownStation(Exception):
    pass


class CatalogUnavailable(Exception):
    pass


class GasolineDatabase:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            # WAL permits concurrent readers during durable report/catalog writes.
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('PRAGMA busy_timeout=5000')
        try:
            yield db
        finally:
            db.close()

    def ready(self):
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')  # Ensure the store is writable.
                db.execute('SELECT 1 FROM price_reports LIMIT 1').fetchone()
                db.execute('SELECT 1 FROM current_prices LIMIT 1').fetchone()
                count = db.execute('SELECT count(*) FROM stations').fetchone()[0]
                db.rollback()
                return count > 0
        except (sqlite3.Error, OSError):
            return False

    def require_catalog(self):
        if not self.ready():
            raise CatalogUnavailable('The persisted S7 station catalog or report store is unavailable')

    def import_stations(self, stations, source, updated_at):
        """Upsert a verified external station directory; never invent a price."""
        if not source or not source.strip():
            raise ValueError('An explicit catalog source is required')
        with self._lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for station in stations:
                db.execute('''INSERT INTO stations
                  (station_id,name,address,latitude,longitude,available_fuel_types,catalog_source,catalog_updated_at)
                  VALUES (?,?,?,?,?,?,?,?)
                  ON CONFLICT(station_id) DO UPDATE SET
                    name=excluded.name, address=excluded.address,
                    latitude=excluded.latitude,longitude=excluded.longitude,
                    available_fuel_types=excluded.available_fuel_types,
                    catalog_source=excluded.catalog_source,catalog_updated_at=excluded.catalog_updated_at''',
                  (station['station_id'], station['name'], station['address'], station['latitude'],
                   station['longitude'], json.dumps(station['available_fuel_types']),source,updated_at))
            db.commit()

    def get_station_rows(self, station_id=None, bounds=None):
        where = ''
        params = []
        if station_id is not None:
            where = 'WHERE s.station_id = ?'
            params = [station_id]
        elif bounds is not None:
            where = 'WHERE s.latitude BETWEEN ? AND ? AND s.longitude BETWEEN ? AND ?'
            params = list(bounds)
        with self.connect() as db:
            return [dict(r) for r in db.execute(f'''SELECT s.*, p.price_report_id,
                 p.fuel_type, p.price_millis, p.currency, p.observed_at, p.submitted_at,
                 p.verification_status, latest.last_accepted_report_at
                 FROM stations s
                 LEFT JOIN current_prices cp ON cp.station_id = s.station_id
                 LEFT JOIN price_reports p ON p.price_report_id = cp.price_report_id
                 LEFT JOIN (
                    SELECT station_id, MAX(submitted_at) AS last_accepted_report_at
                    FROM price_reports GROUP BY station_id
                 ) latest ON latest.station_id = s.station_id
                 {where} ORDER BY s.station_id, p.fuel_type''', params)]

    def create_report(self, station_id, principal, idem_key, payload, received_at):
        """Atomically store the report, current-price projection and 24h replay mapping."""
        body = {
            'fuel_type': payload.fuel_type,
            'price_millis': int(payload.price_per_litre * 1000),
            'currency': payload.currency,
            'observed_at': to_iso(payload.observed_at) if payload.observed_at else None,
        }
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        # Time is assigned when the database transaction accepts the report,
        # not at initial HTTP receipt (which may precede validation/locking).
        request_at = to_iso(received_at)
        with self._lock, self.connect() as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM submission_idempotency WHERE expires_at <= ?', (request_at,))
                duplicate = db.execute('''SELECT body_fingerprint, response_json FROM submission_idempotency
                    WHERE subject=? AND station_id=? AND idem_key=?''',
                    (principal.subject, station_id, idem_key)).fetchone()
                if duplicate:
                    if duplicate['body_fingerprint'] != fingerprint:
                        raise IdempotencyConflict()
                    db.commit()
                    return json.loads(duplicate['response_json'])
                row = db.execute('SELECT available_fuel_types FROM stations WHERE station_id=?', (station_id,)).fetchone()
                if row is None:
                    raise UnknownStation()
                if payload.fuel_type not in json.loads(row['available_fuel_types']):
                    raise ValueError('The station is not catalogued for this fuel type')
                accepted_at = utc_now()
                submitted_at = to_iso(accepted_at)
                expires_at = to_iso(accepted_at + timedelta(hours=24))
                report_id = str(uuid.uuid4())
                db.execute('''INSERT INTO price_reports
                   (price_report_id,station_id,fuel_type,price_millis,currency,observed_at,
                    submitted_at,verification_status,reporter_subject)
                    VALUES (?,?,?,?,?,?,?,'unverified',?)''',
                    (report_id, station_id, payload.fuel_type, body['price_millis'], payload.currency,
                     body['observed_at'],submitted_at,principal.subject))
                db.execute('''INSERT INTO current_prices (station_id,fuel_type,price_report_id)
                    VALUES (?,?,?)
                    ON CONFLICT(station_id,fuel_type) DO UPDATE SET price_report_id=excluded.price_report_id
                    WHERE (SELECT submitted_at FROM price_reports WHERE price_report_id=current_prices.price_report_id)
                           < (SELECT submitted_at FROM price_reports WHERE price_report_id=excluded.price_report_id)
                    OR ((SELECT submitted_at FROM price_reports WHERE price_report_id=current_prices.price_report_id)
                            = (SELECT submitted_at FROM price_reports WHERE price_report_id=excluded.price_report_id)
                        AND current_prices.price_report_id < excluded.price_report_id)''',
                    (station_id,payload.fuel_type,report_id))
                response = {
                    'price_report_id': report_id, 'station_id': station_id,
                    'fuel_type': payload.fuel_type, 'price_per_litre': float(payload.price_per_litre),
                    'currency': 'CAD', 'price_unit': 'CAD/L',
                    'observed_at': body['observed_at'], 'submitted_at': submitted_at,
                    'verification_status': 'unverified', 'source': 'user_report',
                    'data_status': 'user_report', 'is_live': False,
                    'is_stale': bool(payload.observed_at and accepted_at - payload.observed_at > timedelta(hours=24)),
                }
                db.execute('''INSERT INTO submission_idempotency
                    (subject,station_id,idem_key,body_fingerprint,response_json,expires_at)
                    VALUES (?,?,?,?,?,?)''',
                    (principal.subject,station_id,idem_key,fingerprint,json.dumps(response),expires_at))
                db.commit()
                return response
            except Exception:
                db.rollback()
                raise
