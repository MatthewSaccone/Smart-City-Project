"""Train and diagnostically evaluate a *candidate* S2 weekday/hour baseline.

Run after the Toronto importer. Does not promote candidate models to approved.
Uses only observations before an explicit time cutoff for training and later
observations for testing. Dataset/reference-speed methodology needs review
before treating macro-F1 as final T026 evidence.
"""

import argparse
import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Run this from the repository root as shown in README.
SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "002_baseline_model.sql"
P75_MIGRATION = Path(__file__).resolve().parents[1] / "sql" / "003_baseline_p75.sql"

TRAIN_BUCKETS = """
INSERT INTO s2_baseline_buckets
    (model_version, grid_size_mdeg, latitude_cell, longitude_cell,
     weekday, local_hour, mean_congestion_score, p75_congestion_score, sample_count)
SELECT %s, %s,
    FLOOR(s.latitude * 1000.0 / %s)::integer,
    FLOOR(s.longitude * 1000.0 / %s)::integer,
    EXTRACT(ISODOW FROM o.started_at AT TIME ZONE 'America/Toronto')::integer - 1,
    EXTRACT(HOUR FROM o.started_at AT TIME ZONE 'America/Toronto')::integer,
    AVG(GREATEST(0.0, LEAST(1.0,
        1.0 - o.estimated_avg_speed_kph / s.avg_85th_percentile_speed_kph))),
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY GREATEST(0.0, LEAST(1.0,
        1.0 - o.estimated_avg_speed_kph / s.avg_85th_percentile_speed_kph))),
    COUNT(*)
FROM speed_observations AS o
JOIN traffic_studies AS s ON s.count_id = o.count_id
WHERE o.started_at < %s
  AND o.vehicle_count > 0
  AND o.estimated_avg_speed_kph IS NOT NULL
  AND s.avg_85th_percentile_speed_kph > 0
GROUP BY 3, 4, 5, 6
"""

# Count actual/predicted classes across all test observations, including those
# where no valid model bucket exists (prediction=NULL). This avoids reporting
# accuracy without disclosing that large geographic/temporal areas are uncovered.
EVALUATE_SQL = """
WITH test AS (
 SELECT
    FLOOR(s.latitude * 1000.0 / 20)::integer AS lat20,
    FLOOR(s.longitude * 1000.0 / 20)::integer AS lon20,
    FLOOR(s.latitude * 1000.0 / 100)::integer AS lat100,
    FLOOR(s.longitude * 1000.0 / 100)::integer AS lon100,
    EXTRACT(ISODOW FROM o.started_at AT TIME ZONE 'America/Toronto')::integer - 1 AS weekday,
    EXTRACT(HOUR FROM o.started_at AT TIME ZONE 'America/Toronto')::integer AS local_hour,
    GREATEST(0.0, LEAST(1.0,
      1.0 - o.estimated_avg_speed_kph / s.avg_85th_percentile_speed_kph)) AS actual_score
 FROM speed_observations AS o
 JOIN traffic_studies AS s ON s.count_id = o.count_id
 WHERE o.started_at >= %s
   AND o.vehicle_count > 0
   AND o.estimated_avg_speed_kph IS NOT NULL
   AND s.avg_85th_percentile_speed_kph > 0
   AND (%s::timestamptz IS NULL OR o.started_at < %s)
), scores AS (
 SELECT t.actual_score,
   CASE WHEN fine.sample_count >= 5 THEN fine.p75_congestion_score
        WHEN coarse.sample_count >= 5 THEN coarse.p75_congestion_score
        ELSE NULL END AS predicted_score
 FROM test AS t
 LEFT JOIN s2_baseline_buckets AS fine ON
   fine.model_version = %s AND fine.grid_size_mdeg = 20
   AND fine.latitude_cell = t.lat20 AND fine.longitude_cell = t.lon20
   AND fine.weekday = t.weekday AND fine.local_hour = t.local_hour
 LEFT JOIN s2_baseline_buckets AS coarse ON
   coarse.model_version = %s AND coarse.grid_size_mdeg = 100
   AND coarse.latitude_cell = t.lat100 AND coarse.longitude_cell = t.lon100
   AND coarse.weekday = t.weekday AND coarse.local_hour = t.local_hour
), classified AS (
 SELECT
   CASE WHEN actual_score < 0.3 THEN 'low'
        WHEN actual_score < 0.6 THEN 'moderate' ELSE 'high' END AS actual,
   CASE WHEN predicted_score IS NULL THEN NULL
        WHEN predicted_score < 0.3 THEN 'low'
        WHEN predicted_score < 0.6 THEN 'moderate' ELSE 'high' END AS predicted
 FROM scores
)
SELECT actual, predicted, COUNT(*) FROM classified GROUP BY actual, predicted
"""

CLASSES = ("low", "moderate", "high")


def diagnostic_metrics(rows):
    """Counts include predicted=None so coverage is never hidden."""
    matrix = Counter({(actual, predicted): int(n) for actual, predicted, n in rows})
    total = sum(matrix.values())
    covered = sum(n for (_, pred), n in matrix.items() if pred is not None)
    per_class = {}
    for label in CLASSES:
        tp = matrix[label, label]
        predicted = sum(n for (a, p), n in matrix.items() if p == label)
        actual = sum(n for (a, p), n in matrix.items() if a == label)
        precision = tp / predicted if predicted else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"support": actual, "precision": precision,
                            "recall": recall, "f1": f1}
    return {
        "macro_f1": sum(m["f1"] for m in per_class.values()) / len(CLASSES),
        "coverage": covered / total if total else 0.0,
        "test_observations": total,
        "evaluated_observations": covered,
        "per_class": per_class,
        "confusion_matrix": [
            {"actual": a, "predicted": p, "count": n}
            for (a, p), n in sorted(matrix.items(), key=lambda x: (x[0][0], str(x[0][1])))
        ],
    }


def train(db_url: str, version: str, cutoff: datetime | None, validation_end: datetime | None = None):
    import psycopg
    trained_at = datetime.now(timezone.utc)
    with psycopg.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
        # SQL schema uses fixed, local version-controlled DDL; no user SQL is accepted.
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
        cur.execute(P75_MIGRATION.read_text(encoding="utf-8"))
        cur.execute("SELECT MAX(started_at) FROM speed_observations")
        last_observation = cur.fetchone()[0]
        if last_observation is None:
            raise ValueError("No traffic observations have been imported.")
        if cutoff is None:
            cutoff = last_observation - timedelta(days=90)
        if cutoff.tzinfo is None or cutoff > last_observation:
            raise ValueError("cutoff must be timezone aware and no later than the latest observation")
        cur.execute("SELECT MAX(started_at) FROM speed_observations WHERE started_at < %s", (cutoff,))
        training_end_at = cur.fetchone()[0]
        if training_end_at is None:
            raise ValueError("No training data exists before the cutoff")
        cur.execute("""
            INSERT INTO s2_baseline_models
                (model_version, trained_at, training_data_end_at, validation_start_at,
                 model_valid_until, approved)
            VALUES (%s, %s, %s, %s, %s, FALSE)
            ON CONFLICT (model_version) DO UPDATE SET
                trained_at = EXCLUDED.trained_at,
                training_data_end_at = EXCLUDED.training_data_end_at,
                validation_start_at = EXCLUDED.validation_start_at,
                model_valid_until = EXCLUDED.model_valid_until,
                validation_macro_f1 = NULL,
                validation_coverage = NULL,
                validation_observations = NULL,
                evaluated_observations = NULL,
                approved = FALSE
        """, (version, trained_at, training_end_at, cutoff,
              trained_at + timedelta(days=90)))
        cur.execute("DELETE FROM s2_baseline_buckets WHERE model_version = %s", (version,))
        for size in (20, 100):
            cur.execute(TRAIN_BUCKETS, (version, size, size, size, cutoff))
        cur.execute(EVALUATE_SQL, (cutoff, validation_end, validation_end, version, version))
        results = diagnostic_metrics(cur.fetchall())
        if not results["test_observations"]:
            raise ValueError("No held-out observations exist after cutoff")
        cur.execute("""
            UPDATE s2_baseline_models
            SET validation_macro_f1 = %s,
                validation_coverage = %s,
                validation_observations = %s,
                evaluated_observations = %s
            WHERE model_version = %s
        """, (results["macro_f1"], results["coverage"],
              results["test_observations"], results["evaluated_observations"], version))
    # Even a high diagnostic score does NOT auto-approve this model. Study-level
    # reference speeds may include measurements from the holdout period.
    return {"model_version": version, "statistic": "75th_percentile",
            "validation_end": validation_end.isoformat() if validation_end else None,
            "cutoff": cutoff.isoformat(),
            "training_data_end_at": training_end_at.isoformat(),
            "approved": False, "methodology_review_required": True, **results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-version", default="toronto-p75-candidate-v1")
    parser.add_argument("--cutoff", help="ISO UTC instant, e.g. 2026-08-01T00:00:00Z")
    parser.add_argument("--validation-end", default="2026-10-01T00:00:00Z",
                        help="Exclusive validation end, preserving October for later testing")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()
    db_url = os.environ.get("S2_DATABASE_URL")
    if not db_url:
        parser.error("S2_DATABASE_URL must be set in this terminal")
    cutoff = None
    if args.cutoff:
        cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00"))
    validation_end = datetime.fromisoformat(args.validation_end.replace("Z", "+00:00"))
    if validation_end.tzinfo is None or (cutoff is not None and validation_end <= cutoff):
        parser.error("validation-end must be timezone-aware and after cutoff")
    result = train(db_url, args.model_version, cutoff, validation_end)
    report = json.dumps(result, indent=2)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
