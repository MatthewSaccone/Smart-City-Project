-- Add a percentile column while retaining mean values for earlier diagnostics.
-- Safe to run repeatedly on the existing S2 database.
ALTER TABLE s2_baseline_buckets
    ADD COLUMN IF NOT EXISTS p75_congestion_score DOUBLE PRECISION
    CHECK (p75_congestion_score BETWEEN 0 AND 1);
