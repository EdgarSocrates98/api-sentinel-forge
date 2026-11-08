# ADR-008 — Incident Storage: Current State + Journal

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Incidents need both fast current-state queries and a faithful
transition history (timeline, audit, reopen). Full event sourcing for
the whole product adds replay machinery everywhere for local benefit
that is unevenly needed.

## Decision Drivers

- `incidents` queries (list, status, severity) are hot paths.
- `incident_events` must preserve every transition with provenance.
- A closed incident is modified only via new journal events (invariant).

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Current-state row + event journal for incidents | `incidents` + `incident_events` tables | simple reads, complete history where it matters |
| B — Universal event sourcing | every entity is event-sourced | heavy machinery product-wide; unjustified for MVP |

## Decision

Incident architecture uses current state plus a journal of events —
not integral event sourcing for the entire product.

## Consequences

**Positive**

- Timeline and audit reconstructible from the journal alone.
- Current-state reads stay O(1)-ish without projection replay.

**Negative**

- Two write targets per transition — must stay transactional.
- Other entities keep plain CRUD+revision, so their history needs
  audit_events rather than per-entity journals.

## Compatibility

External consumers see incident state + timeline via
`/v1/incidents/{id}` and `/timeline`; the journal is the source of
truth for transitions.

## Reversal Strategy

The journal already captures transitions; migrating to fuller event
sourcing later is a projection-building exercise over
`incident_events` rather than a data loss path. Downgrade: the
current-state table remains the compatibility read model.

## Links

- Blueprint `prompt_inicial.md` §8.1, §11.1, §23 (ADR-008)
- `docs/09-incident-lifecycle.md`, `docs/10-storage-and-retention.md`
