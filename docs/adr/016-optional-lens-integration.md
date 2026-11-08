# ADR-016 — Optional Forge Lens Integration (Referenced Traces)

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Agentic investigations (Cycle 8) generate agent execution traces. Forge
Lens already owns agent/ tool/ execution observability — ingestion
pipeline, persistence and API for agentic traces.

## Decision Drivers

- Building a second agent APM is explicitly out of bounds
  (responsibility contract).
- Investigations need verifiable references to agent runs, not a
  second copy of every span.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Optional Lens integration; Sentinel references traces by id + provenance | evidence refs into Lens' own store | no trace duplication; honors ownership |
| B — Single store for all traces inside Sentinel | Sentinel ingests and stores full agent traces | duplicate agent APM; storage and freshness problems |

## Decision

Lens integration is optional (ADR-degradation contract applies).
Sentinel stores trace/session ids with provenance as investigation
evidence — never a full copy of Lens traces.

## Consequences

**Positive**

- Lens absent → agentic evidence is simply absent/UNAVAILABLE, rest
  of investigation intact.
- Agent-trace retention stays a Lens concern.

**Negative**

- Cross-product navigation depends on Lens being installed.
- Referenced traces could be purged by Lens' own retention —
  investigations record provenance but can't freeze the payload.

## Compatibility

Evidence artifacts referencing Lens carry `source` + `provenance`
fields; no Lens schema is imported. Contract impact: none — reference
by id is adapter-level.

## Reversal Strategy

If Lens is replaced, only the adapter changes: stored references are
ids + provenance strings, so a new backend needs a resolver, not a
data migration. Embedding traces would be a new ADR with a real cost
argument.

## Links

- Blueprint `prompt_inicial.md` §13.3, §23 (ADR-016)
- `docs/06-integration-contracts.md`
