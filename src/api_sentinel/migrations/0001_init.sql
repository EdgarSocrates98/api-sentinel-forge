-- 0001_init.sql — Cycle 1 core schema (versioned; applied via migrations.py)

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_environments (
    environment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL DEFAULT 'workspace',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_services (
    service_id TEXT PRIMARY KEY,
    service_name TEXT NOT NULL,
    environment_id TEXT NOT NULL REFERENCES service_environments(environment_id),
    namespace TEXT,
    display_name TEXT,
    external_refs TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (environment_id, service_name)
);

CREATE TABLE IF NOT EXISTS api_operations (
    operation_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    protocol TEXT NOT NULL DEFAULT 'http',
    method TEXT,
    route TEXT,
    normalized_route TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_releases (
    release_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    version TEXT NOT NULL,
    deployed_at TEXT,
    source TEXT NOT NULL DEFAULT 'declared',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_sources (
    source_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    enabled INTEGER NOT NULL DEFAULT 1,
    config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observation_batches (
    batch_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES telemetry_sources(source_id),
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    signal_types TEXT NOT NULL DEFAULT '[]',
    quality TEXT NOT NULL DEFAULT 'UNKNOWN',
    item_count INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT UNIQUE,
    adapter_version TEXT,
    transform_notes TEXT
);

CREATE TABLE IF NOT EXISTS evidence_artifacts (
    evidence_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    loss_notes TEXT,
    source_id TEXT,
    blob_ref TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
    source_id TEXT PRIMARY KEY REFERENCES telemetry_sources(source_id),
    checkpoint TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_services_env ON api_services(environment_id);
CREATE INDEX IF NOT EXISTS idx_operations_service ON api_operations(service_id);
CREATE INDEX IF NOT EXISTS idx_sources_service ON telemetry_sources(service_id);
CREATE INDEX IF NOT EXISTS idx_batches_source ON observation_batches(source_id);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_events(target_id);
