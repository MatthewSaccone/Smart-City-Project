-- S2-only candidate model tables. Run against traffic_predictor, never the Registry DB.
CREATE TABLE IF NOT EXISTS s2_baseline_models (
    model_version TEXT PRIMARY KEY,
    trained_at TIMESTAMPTZ NOT NULL,
    training_data_end_at TIMESTAMPTZ NOT NULL,
    validation_start_at TIMESTAMPTZ NOT NULL,
    model_valid_until TIMESTAMPTZ NOT NULL,
    validation_macro_f1 DOUBLE PRECISION,
    validation_coverage DOUBLE PRECISION,
    validation_observations BIGINT,
    evaluated_observations BIGINT,
    -- Always false for an unevaluated or unreviewed candidate.
    approved BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS s2_baseline_buckets (
    model_version TEXT NOT NULL REFERENCES s2_baseline_models(model_version),
    grid_size_mdeg INTEGER NOT NULL CHECK (grid_size_mdeg IN (20, 100)),
    latitude_cell INTEGER NOT NULL,
    longitude_cell INTEGER NOT NULL,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    local_hour INTEGER NOT NULL CHECK (local_hour BETWEEN 0 AND 23),
    mean_congestion_score DOUBLE PRECISION NOT NULL
        CHECK (mean_congestion_score BETWEEN 0 AND 1),
    sample_count BIGINT NOT NULL CHECK (sample_count > 0),
    PRIMARY KEY (
        model_version, grid_size_mdeg, latitude_cell,
        longitude_cell, weekday, local_hour
    )
);
