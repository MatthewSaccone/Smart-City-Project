-- S2 owns this database. Do not apply this to the Service Registry database.
CREATE TABLE IF NOT EXISTS traffic_studies (
    count_id BIGINT PRIMARY KEY,
    count_type TEXT NOT NULL CHECK (count_type = 'ATR_SPEED_VOLUME'),
    centreline_id BIGINT NOT NULL,
    location_name TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    study_start_date DATE NOT NULL,
    study_end_date DATE NOT NULL,
    avg_speed_kph DOUBLE PRECISION,
    avg_85th_percentile_speed_kph DOUBLE PRECISION,
    avg_daily_volume DOUBLE PRECISION,
    CHECK (study_start_date <= study_end_date)
);

-- Source records have timezone-naive timestamps. Importer requires an explicit
-- source timezone and records the interpretation used for UTC conversion.
CREATE TABLE IF NOT EXISTS speed_observations (
    id BIGINT PRIMARY KEY, -- raw dataset 'id'; NOT the study's count_id
    count_id BIGINT NOT NULL REFERENCES traffic_studies(count_id),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    direction TEXT NOT NULL,
    speed_bins JSONB NOT NULL, -- original 14 speed-bin vehicle counts
    vehicle_count INTEGER NOT NULL CHECK (vehicle_count >= 0),
    estimated_avg_speed_kph DOUBLE PRECISION,
    source_timezone TEXT NOT NULL,
    CHECK (ended_at > started_at),
    CHECK (estimated_avg_speed_kph IS NULL OR estimated_avg_speed_kph >= 0),
    CHECK ((vehicle_count = 0) = (estimated_avg_speed_kph IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_studies_location
    ON traffic_studies(latitude, longitude);
CREATE INDEX IF NOT EXISTS ix_studies_centreline
    ON traffic_studies(centreline_id);
CREATE INDEX IF NOT EXISTS ix_observations_study_time
    ON speed_observations(count_id, started_at);
CREATE INDEX IF NOT EXISTS ix_observations_time
    ON speed_observations(started_at);

-- A successful import records *when S2 last synced these files*.
-- This is not the observation timestamp or proof that Toronto has new data.
CREATE TABLE IF NOT EXISTS traffic_import_metadata (
    dataset_id TEXT PRIMARY KEY,
    imported_at TIMESTAMPTZ NOT NULL,
    refresh_due_at TIMESTAMPTZ NOT NULL,
    latest_observation_at TIMESTAMPTZ,
    source_timezone TEXT NOT NULL,
    imported_observation_count BIGINT NOT NULL CHECK (imported_observation_count >= 0)
);
