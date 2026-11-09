-- 0005_integrations.sql — Cycle 5: cross-Forge intelligence.
--
-- change_events records deploy/rollback/config/flag/contract/dependency/
-- maintenance changes correlated during investigation (docs/03 ChangeEvent).
-- `at` is event time; `created_at` is ingest time — never conflated.
-- change_id is deterministic (content hash) so re-imports are idempotent.
--
-- handoff_records audits every delegation to a specialist Forge
-- (docs/03 ExternalHandoff): the envelope built, the transport used, the
-- outcome. status is a named terminal state — never a hang, never a
-- fabricated success (degradation contract, docs/06).
-- request_ref/response_ref are references (file paths / envelope ids),
-- never payloads.

CREATE TABLE IF NOT EXISTS change_events (
    change_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES api_services(service_id),
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    ref TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoff_records (
    handoff_id TEXT PRIMARY KEY,
    to_forge TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    request_ref TEXT,
    response_ref TEXT,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_change_events_service
    ON change_events(service_id, at);
CREATE INDEX IF NOT EXISTS idx_change_events_kind
    ON change_events(service_id, kind, at);
CREATE INDEX IF NOT EXISTS idx_handoff_records_forge
    ON handoff_records(to_forge, created_at);
CREATE INDEX IF NOT EXISTS idx_handoff_records_status
    ON handoff_records(status);

-- dependency_observations is the Sentinel-owned *operational* graph
-- (docs/03 DependencyObservation): an edge means a call/failure/latency
-- was OBSERVED between service_id and target during a window — it never
-- claims a static code path exists (Code Atlas owns the structural graph;
-- the two stay conceptually distinct, docs/06). edge_id is deterministic
-- over (service, target, operation, window) so re-ingest is idempotent.
CREATE TABLE IF NOT EXISTS dependency_observations (
    edge_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    target TEXT NOT NULL,
    target_kind TEXT NOT NULL DEFAULT 'service',
    operation_id TEXT,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    timeouts INTEGER NOT NULL DEFAULT 0,
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    provenance TEXT NOT NULL DEFAULT 'observed',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_depobs_service
    ON dependency_observations(service_id, window_start);
CREATE INDEX IF NOT EXISTS idx_depobs_target
    ON dependency_observations(target);
