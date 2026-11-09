-- 0004_incidents.sql — Cycle 4: deterministic detection + incident lifecycle.
--
-- detection_rules is immutable per (rule_id, revision): changing a rule
-- creates a new revision, never rewrites history (invariant).
-- anomalies dedup by deterministic anomaly_id — replay is idempotent.
-- incidents is the current-state read model (ADR-008); revision enforces
-- concurrency control on every write.
-- incident_events is the journal: INSERT-only by design — a closed incident
-- mutates only through new journal entries.

CREATE TABLE IF NOT EXISTS detection_rules (
    rule_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    kind TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (rule_id, revision)
);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    operation_id TEXT,
    detected_at TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_revision INTEGER NOT NULL,
    detector_id TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OBSERVED',
    explanation TEXT NOT NULL,
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    unknowns TEXT NOT NULL DEFAULT '[]',
    dedup_key TEXT NOT NULL DEFAULT '',
    anomaly TEXT NOT NULL,
    incident_id TEXT REFERENCES incidents(incident_id)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    operation_id TEXT,
    group_key TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    acknowledged_at TEXT,
    resolved_at TEXT,
    owner TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_events (
    event_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    note TEXT,
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_detection_rules_service
    ON detection_rules(service_id, rule_id, revision);
CREATE INDEX IF NOT EXISTS idx_anomalies_service
    ON anomalies(service_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_anomalies_incident
    ON anomalies(incident_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_rule_scope
    ON anomalies(service_id, rule_id, operation_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_incidents_status
    ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_group
    ON incidents(service_id, group_key);
CREATE INDEX IF NOT EXISTS idx_incident_events_incident
    ON incident_events(incident_id, at);
