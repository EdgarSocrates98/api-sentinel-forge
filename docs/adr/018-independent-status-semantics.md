# ADR-018 — Independent Status Semantics (No Universal Health Score)

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

A single "health 87%" number flattens distinct dimensions — data
quality, availability, SLO compliance, incident severity — into an
unexplainable score users cannot verify.

## Decision Drivers

- Quality state ≠ operational conclusion (ADR-017); incident severity
  ≠ HTTP status code (docs/09); missing traffic ≠ outage.
- UX principle: explainable criteria over arbitrary aggregates —
  states `AVAILABLE | UNAVAILABLE | PARTIAL | UNVERIFIED`.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Independent, composable status semantics per dimension | availability, SLO assessment, source quality, incident severity reported separately with criteria | every state is explainable and linkable to evidence |
| B — One universal health score | weighted composite | unverifiable number; hides partial data and unknowns |

## Decision

No single health score. Each surface shows the specific states with
their evidence: per-service availability, per-SLO assessment, per-
source quality, per-incident severity — computed from explicit
criteria the user can inspect.

## Consequences

**Positive**

- UNVERIFIED is a first-class honest answer instead of a greyed score.
- Severity stays impact-driven; 4xx doesn't auto-degrade "health".

**Negative**

- More UI elements to design per screen.
- Users wanting one number must read SLO + incident views.

## Compatibility

API responses expose the separate fields (availability state, quality
state, SLO assessment, severity); consumers must not collapse them
into a composite.

## Reversal Strategy

If a composite summary is ever demanded, it is computed client-side
from the published fields — the API already ships the components, so
no migration; the honest per-dimension semantics remain canonical.

## Links

- Blueprint `prompt_inicial.md` §9.3, §11.3, §18.3, §23 (ADR-018)
- `docs/09-incident-lifecycle.md`, `docs/11-portal-ux-design.md`
