-- 0006_portal.sql — Cycle 6: Portal API Operations surface (docs/04).
--
-- investigations records one controlled investigation launched over the
-- local HTTP API (POST /v1/incidents/{id}/investigations). status is a named
-- state (QUEUED|RUNNING|COMPLETED|FAILED), never a fabricated success;
-- context_pack carries the assembled bounded pack (incident + anomalies +
-- evidence refs + latest indicators + structural context when the Atlas
-- adapter has data); idempotency_key dedups client retries (NULL = no key).
--
-- ingestion_jobs records one authorized import initiated over HTTP
-- (POST /v1/ingestion/jobs). Execution is bounded-synchronous — the pipeline
-- runs inside the request — but every transition is recorded so the job
-- resource stays auditable (queued → running → completed|failed).
-- idempotency_key dedups re-posts: explicit client key or derived
-- sha256(source_id|file_ref|content) — a replay returns the same job.

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    status TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '{}',
    context_pack TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES telemetry_sources(source_id),
    file_ref TEXT NOT NULL,
    kind TEXT,
    status TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0,
    skipped_duplicates INTEGER NOT NULL DEFAULT 0,
    batch_id TEXT,
    error TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_investigations_incident
    ON investigations(incident_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_source
    ON ingestion_jobs(source_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status
    ON ingestion_jobs(status);
