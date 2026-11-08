# ADR-002 — Modular Monolith

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

The product needs clear internal boundaries (inventory, ingestion,
detection, incidents, …) but the MVP deploys locally for a single
operator. Premature distribution adds operational cost without benefit.

## Decision Drivers

- Local-first execution model; single user workstation.
- Boundaries must exist *in code* from day one so contexts can be
  promoted later.
- Persisted internal events needed for relevant state changes
  (incident journal, audit).

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Modular monolith + hexagonal | one process, enforced module boundaries, ports/facades | matches deployment reality; extractable later |
| B — Mandatory microservices | per-context services from day one | operational overhead unjustified for local MVP |

## Decision

Modular monolith with hexagonal architecture and persisted internal
events. Initial deployment: a single local CLI process (optional local
FastAPI surface for the Portal later).

## Consequences

**Positive**

- Simple install/debug/backup; one SQLite file.
- Module promotion path preserved via versioned contracts.

**Negative**

- Boundary discipline is by convention + review, not network isolation.
- A context that outgrows the process needs a deliberate extraction.

## Compatibility

Internal only — no external contract impact. Cross-module calls go
through ports, keeping adapters swappable (ADR-006 governs externals).

## Reversal Strategy

Promote a module to its own process/database only when its own metrics
justify it; public contracts are already versioned so extraction does
not re-break consumers. Monitor per-context resource use before acting.

## Links

- Blueprint `prompt_inicial.md` §5.1, §23 (ADR-002)
- `docs/02-architecture-design.md`
