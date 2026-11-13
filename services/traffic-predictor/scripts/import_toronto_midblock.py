"""Import Toronto Midblock speed/volume observations into S2-owned PostgreSQL.

The dataset's timestamps are timezone-naive. The caller MUST specify how to
interpret them. DST-ambiguous or nonexistent wall-clock times are rejected,
not silently assigned a UTC instant.
"""

import argparse
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Allow both the documented direct script command and ordinary test imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.data_events import publish_dataset_updated

SPEED_BINS = (
    ("vol_1_19kph", 1, 19),
    ("vol_20_25kph", 20, 25),
    ("vol_26_30kph", 26, 30),
    ("vol_31_35kph", 31, 35),
    ("vol_36_40kph", 36, 40),
    ("vol_41_45kph", 41, 45),
    ("vol_46_50kph", 46, 50),
    ("vol_51_55kph", 51, 55),
    ("vol_56_60kph", 56, 60),
    ("vol_61_65kph", 61, 65),
    ("vol_66_70kph", 66, 70),
    ("vol_71_75kph", 71, 75),
    ("vol_76_80kph", 76, 80),
    ("vol_81_160kph", 81, 160),
)
SPEED_COLUMNS = {"id", "count_id", "time_start", "time_end", "direction"} | {
    column for column, _, _ in SPEED_BINS
}
SUMMARY_COLUMNS = {
    "count_id", "count_type", "centreline_id", "location_name",
    "latitude", "longitude", "count_date_start", "count_date_end",
    "avg_speed", "avg_85th_percentile_speed", "avg_daily_vol",
}


def read_csv(path, required_columns):
    """Yield CSV rows without loading an entire file into memory."""
    with open(path, "r", encoding="utf-8-sig", newline="") as infile:
        reader = csv.DictReader(infile)
        missing = required_columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing required columns: {sorted(missing)}")
        yield from reader


def optional_float(value):
    return float(value) if value not in (None, "") else None


def study_from_row(row):
    if row["count_type"] != "ATR_SPEED_VOLUME":
        return None
    return (
        int(row["count_id"]),
        row["count_type"],
        int(row["centreline_id"]),
        row["location_name"],
        float(row["latitude"]),
        float(row["longitude"]),
        datetime.strptime(row["count_date_start"], "%Y-%m-%d").date(),
        datetime.strptime(row["count_date_end"], "%Y-%m-%d").date(),
        optional_float(row["avg_speed"]),
        optional_float(row["avg_85th_percentile_speed"]),
        optional_float(row["avg_daily_vol"]),
    )


def parse_source_datetime(value, source_tz):
    """Convert naive source wall time to UTC, rejecting DST uncertainty."""
    raw = datetime.fromisoformat(value.strip())
    if raw.tzinfo is not None:
        return raw.astimezone(timezone.utc)

    a = raw.replace(tzinfo=source_tz, fold=0)
    b = raw.replace(tzinfo=source_tz, fold=1)
    a_valid = a.astimezone(timezone.utc).astimezone(source_tz).replace(tzinfo=None) == raw
    b_valid = b.astimezone(timezone.utc).astimezone(source_tz).replace(tzinfo=None) == raw
    if not a_valid and not b_valid:
        raise ValueError(f"Nonexistent local time during DST transition: {value}")
    if a_valid and b_valid and a.utcoffset() != b.utcoffset():
        raise ValueError(f"Ambiguous local time during DST transition: {value}")
    return (a if a_valid else b).astimezone(timezone.utc)


def observation_from_row(row, source_tz):
    bins = {}
    weighted_total = 0.0
    n = 0
    for column, low, high in SPEED_BINS:
        quantity = int(row[column])
        if quantity < 0:
            raise ValueError(f"Negative vehicle count: {column}")
        bins[column] = quantity
        n += quantity
        weighted_total += quantity * (low + high) / 2

    start = parse_source_datetime(row["time_start"], source_tz)
    end = parse_source_datetime(row["time_end"], source_tz)
    if not 0 < (end - start).total_seconds() <= 30 * 60:
        raise ValueError(f"Unexpected interval: {row['time_start']} to {row['time_end']}")

    return (
        int(row["id"]), int(row["count_id"]), start, end,
        row["direction"].strip(), bins, n,
        round(weighted_total / n, 4) if n else None,
        source_tz.key,
    )


def study_rows(summary_path):
    studies = {}
    for row in read_csv(summary_path, SUMMARY_COLUMNS):
        study = study_from_row(row)
        if study is not None:
            studies[study[0]] = study
    return studies


def iter_observations(path, studies, source_tz, stats, max_rows=None, strict=False):
    """Yield valid observations and update the supplied running statistics."""
    for row in read_csv(path, SPEED_COLUMNS):
        if max_rows is not None and stats["scanned"] >= max_rows:
            break
        stats["scanned"] += 1
        try:
            if int(row["count_id"]) not in studies:
                raise ValueError("No matching ATR_SPEED_VOLUME study in summary")
            record = observation_from_row(row, source_tz)
        except (ValueError, TypeError, KeyError) as exc:
            stats["skipped"] += 1
            if len(stats["reasons"]) < 10:
                stats["reasons"].append(f"source row {stats['scanned']}: {exc}")
            if strict:
                raise ValueError(stats["reasons"][-1]) from exc
            continue
        stats["accepted"] += 1
        yield record


def load_schema(conn, schema_path):
    with conn.cursor() as cursor:
        cursor.execute(schema_path.read_text(encoding="utf-8"))
    conn.commit()


def import_studies(conn, studies):
    fields = (
        "count_id", "count_type", "centreline_id", "location_name", "latitude",
        "longitude", "study_start_date", "study_end_date", "avg_speed_kph",
        "avg_85th_percentile_speed_kph", "avg_daily_volume",
    )
    names = ", ".join(fields)
    placeholders = ", ".join(["%s"] * len(fields))
    updates = ", ".join(f"{field} = EXCLUDED.{field}" for field in fields if field != "count_id")
    query = (f"INSERT INTO traffic_studies ({names}) VALUES ({placeholders}) "
             f"ON CONFLICT (count_id) DO UPDATE SET {updates}")
    with conn.cursor() as cursor:
        cursor.executemany(query, studies.values())
    conn.commit()


def copy_batch(conn, batch):
    """Efficient staging COPY + UPSERT; re-importing data is idempotent."""
    from psycopg.types.json import Jsonb

    columns = (
        "id", "count_id", "started_at", "ended_at", "direction", "speed_bins",
        "vehicle_count", "estimated_avg_speed_kph", "source_timezone",
    )
    names = ", ".join(columns)
    with conn.cursor() as cursor:
        cursor.execute("TRUNCATE TABLE stage_speed_observations")
        with cursor.copy(f"COPY stage_speed_observations ({names}) FROM STDIN") as cp:
            for row in batch:
                cp.write_row((*row[:5], Jsonb(row[5]), *row[6:]))
        cursor.execute(
            f"INSERT INTO speed_observations ({names}) "
            f"SELECT {names} FROM stage_speed_observations WHERE TRUE "
            "ON CONFLICT (id) DO NOTHING"
        )
        inserted = cursor.rowcount
    conn.commit()
    return inserted



def record_import_metadata(conn, source_tz, refresh_days):
    """Mark a successful local sync; preserve latest observed event separately."""
    from datetime import timedelta
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*), MAX(ended_at) FROM speed_observations")
        count, latest = cursor.fetchone()
        if count == 0:
            raise ValueError("No speed observations imported; refusing to mark dataset ready")
        imported_at = datetime.now(timezone.utc)
        cursor.execute("""
            INSERT INTO traffic_import_metadata
                (dataset_id, imported_at, refresh_due_at, latest_observation_at,
                 source_timezone, imported_observation_count)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (dataset_id) DO UPDATE SET
                imported_at = EXCLUDED.imported_at,
                refresh_due_at = EXCLUDED.refresh_due_at,
                latest_observation_at = EXCLUDED.latest_observation_at,
                source_timezone = EXCLUDED.source_timezone,
                imported_observation_count = EXCLUDED.imported_observation_count
        """, ("toronto_midblock_2025_2029", imported_at,
              imported_at + timedelta(days=refresh_days), latest, source_tz.key, count))
        publish_dataset_updated(cursor, dataset_id="toronto_midblock_2025_2029", updated_at=imported_at,
                                refresh_due_at=imported_at + timedelta(days=refresh_days),
                                latest_observation_at=latest)
    conn.commit()

def run(args):
    try:
        tz = ZoneInfo(args.source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA source timezone: {args.source_timezone}") from exc
    studies = study_rows(args.summary)
    print(f"Loaded {len(studies):,} ATR_SPEED_VOLUME studies from summary")
    if not studies:
        raise ValueError("No speed/volume studies present")

    conn = None
    if not args.dry_run:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary] before PostgreSQL import") from exc
        dsn = args.database_url or os.getenv("S2_DATABASE_URL")
        if not dsn:
            raise ValueError("Provide --database-url or S2_DATABASE_URL")
        conn = psycopg.connect(dsn)
        schema = Path(__file__).resolve().parents[1] / "sql" / "001_toronto_midblock.sql"
        load_schema(conn, schema)
        import_studies(conn, studies)
        with conn.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE stage_speed_observations "
                           "(LIKE speed_observations) ON COMMIT PRESERVE ROWS")
        conn.commit()

    stats = {"scanned": 0, "accepted": 0, "skipped": 0, "reasons": []}
    batch = []
    inserted_total = 0
    try:
        for record in iter_observations(
            args.speed, studies, tz, stats, args.max_rows, args.strict
        ):
            if conn is not None:
                batch.append(record)
                if len(batch) >= args.batch_size:
                    inserted_total += copy_batch(conn, batch)
                    batch.clear()
        if conn is not None and batch:
            inserted_total += copy_batch(conn, batch)
        if conn is not None:
            record_import_metadata(conn, tz, args.refresh_days)
    finally:
        if conn is not None:
            conn.close()

    print(f"Parsed: {stats['accepted']:,}; scanned: {stats['scanned']:,}; "
          f"skipped: {stats['skipped']:,}")
    if conn is not None:
        print(f"New PostgreSQL observation rows inserted: {inserted_total:,}")
    for reason in stats["reasons"]:
        print(f"  WARNING: {reason}")
    if args.dry_run:
        print("DRY RUN ONLY: no database connection or changes")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True, help="svc_summary_data.csv")
    parser.add_argument("--speed", type=Path, required=True, help="full raw-speed CSV or sample TXT")
    parser.add_argument("--source-timezone", required=True,
                        help="Explicit IANA interpretation of timezone-naive source timestamps")
    parser.add_argument("--database-url", help="PostgreSQL connection URL (or S2_DATABASE_URL)")
    parser.add_argument("--max-rows", type=int, help="Scan at most this many raw records")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--refresh-days", type=int, default=90,
                        help="Demo synchronization due interval (not observation freshness)")
    parser.add_argument("--dry-run", action="store_true", help="Validate without connecting to PostgreSQL")
    parser.add_argument("--strict", action="store_true", help="Stop on first invalid observation")
    args = parser.parse_args(argv)
    if args.max_rows is not None and args.max_rows < 1:
        parser.error("--max-rows must be positive")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.refresh_days < 1:
        parser.error("--refresh-days must be positive")
    run(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as err:
        print(f"Import failed: {err}", file=sys.stderr)
        raise SystemExit(1) from err
