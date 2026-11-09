-- 0002_ingestion.sql — Cycle 2: runtime observations + ingestion quarantine.
-- Observations are append-only: a redelivery with identical content is deduplicated
-- via idempotency_key, a conflicting one is quarantined — never rewritten in place.

CREATE TABLE IF NOT EXISTS runtime_observations (
    observation_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    operation_id TEXT,
    release_id TEXT,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    ingest_time TEXT NOT NULL,
    processing_time TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES telemetry_sources(source_id),
    batch_id TEXT NOT NULL REFERENCES observation_batches(batch_id),
    quality TEXT NOT NULL DEFAULT 'UNKNOWN',
    measurement TEXT NOT NULL,
    attributes TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS ingestion_quarantine (
    item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES observation_batches(batch_id),
    reason TEXT NOT NULL,
    payload TEXT,
    payload_truncated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_service_window
    ON runtime_observations(service_id, window_start);
CREATE INDEX IF NOT EXISTS idx_observations_batch
    ON runtime_observations(batch_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_batch
    ON ingestion_quarantine(batch_id);
