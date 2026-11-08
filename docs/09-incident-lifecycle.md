# 09 — Incident Lifecycle

Incident intelligence is the product's identity: a dashboard explains
current state, the Sentinel preserves operational reasoning and turns
evidence into decisions.

## Lifecycle states

An incident carries an **operational status** (workflow state machine:
detected → investigating → mitigated/resolved, with reopen and
close-with-history transitions recorded in the journal) and an
independent **confidence classification** — never conflated.

Example: an incident can be `INVESTIGATING` while holding a `PROBABLE`
hypothesis; that does not prove the cause. A closed incident is modified
only through new journal events (invariant; see 03-domain-model).

## Incident model

| Field | Purpose |
| --- | --- |
| `incident_id` | stable identity |
| `title` | objective description |
| `severity` | operational impact |
| `status` | workflow state |
| `service_refs` | affected services |
| `operation_refs` | affected operations |
| `first_observed_at` | first observed signal |
| `detected_at` | detection moment |
| `acknowledged_at` | acknowledgment moment |
| `resolved_at` | observed recovery |
| `rule_refs` | detectors that fired |
| `evidence_refs` | verifiable evidence |
| `hypotheses` | candidate causes |
| `change_refs` | correlated changes |
| `owner` | responsible, when assigned |
| `actions` | executed checks/actions |
| `limitations` | explicit gaps |
| `revision` | concurrency control |

## Severity — impact-driven, not HTTP-driven

| Severity | Example |
| --- | --- |
| SEV1 | critical business flow broadly unavailable |
| SEV2 | significant degradation of a critical service |
| SEV3 | partial or limited impact |
| SEV4 | anomaly without proven relevant impact |
| INFORMATIONAL | observation for analysis, no operational incident |

Real criteria are defined per service and per organization. A single
HTTP 500 never auto-produces SEV1.

## Deduplication and grouping

Grouping by service name alone is insufficient. Grouping considers:

- service and operation;
- temporal window;
- detector and anomaly signature;
- possibly shared dependency;
- environment;
- deployment version;
- related active incidents;
- source identity;
- evidence of common failure.

Dedup preserves the original events and records *why* anomalies were
associated to the same incident. Reprocessing a batch never creates a
duplicate incident (Cycle 4 gate).

## Causal hypotheses — confidence ladder

```
OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED
```

A hypothesis becomes `VERIFIED` only when an independent tool or
procedure supplies sufficient evidence. No fake precision: no "97%
confidence" without a calibrated model — explainable states and
observable evidence instead.

Worked example (`INC-2026-0042`, hypothetical, no real data):

| Ladder rung | Content |
| --- | --- |
| Observed fact | p95 of `POST /payments` rose in the analyzed window |
| Temporal correlation | a new version deployed shortly before degradation |
| Candidate hypothesis | the version may have changed the payment-dependency timeout policy |
| Verification status | NOT VERIFIED |
| Next recommended check | diff previous/current config via authorized tools; correlate with dependency traces |

## Storage model

Current state plus journal (`incidents` + `incident_events` tables) —
not full event sourcing across the product (ADR-008). The current-state
row answers queries fast; the journal preserves every transition with
provenance so timelines and audits are reproducible.
