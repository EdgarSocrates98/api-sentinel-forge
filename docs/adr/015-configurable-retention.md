# ADR-015 — Configurable Retention

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Telemetry accumulates: detailed observations, per-minute and hourly
aggregates, incidents, evidence, audit. Infinite history exhausts local
storage; arbitrary deletion destroys investigations and auditability.

## Decision Drivers

- Suggested initial tiers exist (7d / 30d / 90d / 180d) but no period
  is universal — legal and organizational requirements differ.
- Silent loss of evidence is a zero-tolerance engineering target.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Configurable per-class retention + evidence pinned to incident retention + separate audit policy + active-investigation guard | deletion respects references | bounded storage, honest loss semantics |
| B — Infinite history | keep everything forever | unbounded local growth; no lifecycle story |

## Decision

Retention is configurable per data class with the blueprint tiers as
initial defaults; incident evidence lives and dies with its incident;
audit trail follows a separate access-controlled policy; personal data
is minimized and removed per purpose. **Automatic deletion never
destroys evidence referenced by active investigations.**

## Consequences

**Positive**

- Storage footprint predictable on a workstation.
- Purge policy gives PII minimization a concrete mechanism (LGPD
  mechanisms, organizational conformity remains the installer's duty).

**Negative**

- Retention jobs must resolve live `evidence_refs` — a correctness-
  critical path needing tests.
- Misconfigured short windows erase investigation context; config
  must warn on aggressive values.

## Compatibility

Retention changes are workspace-local; no external contract impact.
Evidence contracts must remain resolvable while referenced — a
consumer-visible guarantee.

## Reversal Strategy

Extending retention is trivial; recovering wrongly-purged data is
restoration from backup — hence tested backup/restore is part of the
same storage spec (docs/10, docs/15).

## Links

- Blueprint `prompt_inicial.md` §8.3, §16.4, §23 (ADR-015)
- `docs/10-storage-and-retention.md`
