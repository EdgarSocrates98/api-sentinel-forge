# ADR-017 — Explicit Data-Quality Contracts

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Observations arrive complete, partial, sampled, stale, late or invalid.
Treating partial data as complete fabricates health; treating sampled
traces as the full population produces wrong global rates (LAB-007,
LAB-008).

## Decision Drivers

- Quality state and operational conclusion are orthogonal dimensions:
  a healthy API can sit behind a partial source; a sound source can
  observe a down API.
- Invariants: unsampled never labeled sampled; missing source never
  yields fabricated health; truncated evidence declares the loss.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Explicit quality contract on every observation: `COMPLETE | PARTIAL | SAMPLED | STALE | INVALID | UNKNOWN` + provenance questions answered per record | honest downstream math | more fields everywhere |
| B — Treat partial data as complete | assume coverage | silent fabrication; violates the product's core honesty promise |

## Decision

Every observation carries a separate data-quality state plus provenance
(who/when/interval/unit/completeness/sampling/aggregation/transform/
gaps/adapter-version). Indicators report absence rather than
interpolate; aggregates and health verdicts must consume quality, not
ignore it.

## Consequences

**Positive**

- "Does not fabricate health" is testable (LAB-007).
- UX can show sampling/gap indicators credibly (docs/11).

**Negative**

- Every adapter must populate quality correctly — review burden.
- Indicators become conditional: "computable" depends on quality.

## Compatibility

`RuntimeObservation/v1` carries the `quality` block; unknown/missing
quality is `UNKNOWN`, never defaulted to `COMPLETE` — consumers must
handle all enum values.

## Reversal Strategy

Quality states are additive-versioned: a new state value means a
contract revision. Dropping the contract is a product honesty break —
not a supported path.

## Links

- Blueprint `prompt_inicial.md` §6.2, §7.2, §23 (ADR-017)
- `docs/03-domain-model.md`, `docs/05-telemetry-contracts.md`
