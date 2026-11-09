-- 0003_reliability.sql — Cycle 3: versioned SLO definitions + windowed assessments.
-- slo_definitions is immutable per (slo_id, revision): changing an SLO creates
-- a new revision, never rewrites an old one (LAB-015).
-- slo_assessments replaces on (slo_id, revision, window_start, window_end):
-- re-evaluating the same slot is idempotent, never a duplicate.

CREATE TABLE IF NOT EXISTS slo_definitions (
    slo_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    definition TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (slo_id, revision)
);

CREATE TABLE IF NOT EXISTS slo_assessments (
    assessment_id TEXT PRIMARY KEY,
    slo_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    sli REAL,
    eligible REAL NOT NULL DEFAULT 0,
    good REAL NOT NULL DEFAULT 0,
    budget_consumed REAL,
    state TEXT NOT NULL,
    assessment TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (slo_id, revision, window_start, window_end)
);

CREATE INDEX IF NOT EXISTS idx_slo_definitions_service
    ON slo_definitions(service_id);
CREATE INDEX IF NOT EXISTS idx_slo_assessments_slo
    ON slo_assessments(slo_id, revision, window_start);
