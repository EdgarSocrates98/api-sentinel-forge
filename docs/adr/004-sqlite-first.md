# ADR-004 — SQLite First

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Persistence must cover transactional metadata/state, temporal
aggregates and evidence references for a local single-writer product.
PostgreSQL or ClickHouse would be mandatory daemons before value
exists.

## Decision Drivers

- Local-first, single operator, one-writer workload (ADR-003).
- Need transactions, FKs, versioned migrations, backup/restore —
  SQLite covers all with zero services.
- Analytical history may eventually exceed SQLite's comfort zone.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — SQLite + evidence files + optional Parquet/DuckDB | WAL mode, FKs, migrations; content-addressed blobs; analytical layer optional | zero-dependency start; honest scale limits |
| B — PostgreSQL mandatory | server-backed from day one | violates local-first install simplicity |

## Decision

SQLite for transactional metadata and state (WAL), immutable/versioned
evidence files, temporal aggregates in SQLite; Parquet for analytical
history when volume justifies, DuckDB as an optional analysis
dependency.

## Consequences

**Positive**

- Backup = file copy + blobs dir; restores testable in labs.
- Idempotency keys, checkpoints and FKs available out of the box.

**Negative**

- Not a distributed high-ingest store; scale limit must be measured
  and documented rather than assumed.
- Single-writer concurrency must be enforced deliberately.

## Compatibility

No external contract impact. Table list and integrity requirements are
fixed in `docs/10`; migrations are versioned forward-only.

## Reversal Strategy

Repository pattern isolates SQL; the migration path is export/import of
the documented table set plus blob dir — doable because evidence is
content-addressed and the journal is replayable into a new store.
Trigger: measured ingest/query limits, documented per §8.2.

## Links

- Blueprint `prompt_inicial.md` §8, §23 (ADR-004)
- `docs/10-storage-and-retention.md`, `docs/15-operations-runbook.md`
