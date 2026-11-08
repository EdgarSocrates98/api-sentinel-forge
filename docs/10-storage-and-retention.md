# 10 — Storage & Retention

Local-first persistence: no mandatory PostgreSQL or ClickHouse. The MVP
stores everything a single operator needs in one workspace (ADR-004).

| Layer | Technology | Content |
| --- | --- | --- |
| Transactional metadata & state | SQLite (WAL) | identities, sources, runs, aggregates, SLOs, rules, anomalies, incidents, handoffs, audit |
| Evidence | immutable/versioned files | content-addressed artifacts referenced by hash from SQLite |
| Temporal aggregates | SQLite | per-window rollups (1 min / 1 h / 24 h) |
| Analytical history | Parquet files | only when volume justifies |
| Historical analysis | DuckDB (optional dep) | ad-hoc queries over Parquet |

SQLite is not used as a distributed high-ingest database: the scale
limit must be measured, documented, and met with additional storage
options only when needed.

## Tables (SQLite)

| Table | Content |
| --- | --- |
| `services` | identity and metadata |
| `environments` | operational scopes |
| `operations` | routes and methods |
| `sources` | source configuration |
| `ingestion_runs` | executions and checkpoints |
| `observations` | observation metadata |
| `metric_series` | series identity |
| `metric_windows` | per-window aggregates |
| `slo_definitions` | definitions and revisions |
| `slo_assessments` | temporal evaluations |
| `detection_rules` | rules and revisions |
| `anomalies` | deviations |
| `incidents` | identities and current states |
| `incident_events` | transition history (journal) |
| `evidence_artifacts` | provenance and hashes |
| `handoffs` | delegated executions |
| `audit_events` | changes and authorizations |

One writer per table; cross-context references by id only.

## Integrity requirements

- WAL mode where appropriate; transactions for related updates.
- Foreign keys; versioned migrations; indexes by service, operation
  and time.
- Ingestion checkpoints and idempotency keys — reprocessing is safe.
- Retention policy, tested backup/restore, corrupt-database recovery.
- Writer-concurrency restriction (single writer per DB file).
- Orphaned evidence-file detection.

## Retention (initial suggested policy — configurable, ADR-015)

| Data | Initial policy |
| --- | --- |
| Detailed local observations | 7 days |
| 1-minute aggregates | 30 days |
| Hourly aggregates | 90 days |
| Incidents | 180 days or defined policy |
| Incident evidence | tied to the incident's retention |
| Audit trail | separate policy, access-controlled |
| Personal data | minimization and removal per purpose |

These periods are not universal: corporate environments need specific
policies including legal/organizational retention requirements.

**Hard rule:** automatic deletion must never destroy evidence referenced
by active investigations — retention jobs check live `evidence_refs`
before purging.
