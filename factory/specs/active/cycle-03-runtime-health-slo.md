---
id: cycle-03-runtime-health-slo
title: "Cycle 3 — Runtime Health & SLO"
agent: claude
risk: medium
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Builds the `metrics` and `reliability` bounded contexts: request aggregations,
first-class indicators per service/operation, versioned SLI/SLO definitions,
error budgets and windowed assessment history. Governed by
`docs/08-slo-and-detection-design.md` (indicator definitions and SLO
formulas), `docs/03-domain-model.md` (`SloDefinition`, `SloAssessment`), and
`docs/02-architecture-design.md` (blueprint §9).

_Depends: cycle-02-telemetry-ingestion (normalized observations, quality
states)._

# Acceptance Criteria

- [x] Request aggregations computed per service/operation per window: request
  rate, error ratio and timeout ratio over eligible requests.
- [x] Latency quantiles (p50/p95/p99) computed only over compatible
  distributions/buckets — percentiles are never averaged.
- [x] Availability SLI (`good / eligible`) and latency SLI
  (`within threshold / eligible`) implemented per docs/08; good/bad
  classification varies per SLO and service.
- [x] Error classification is configurable per service/operation: not all 4xx
  count as unavailability; 401/403/429 get dedicated indicators.
- [x] `SloDefinition` is versioned (`slo_id` + revision) with objective `S`
  and window; changing an SLO creates a new revision — LAB-015 proves the
  prior revision is preserved and the calculation reproducible.
- [x] Error budget implemented exactly as `B = N(1−S)` and
  `budget consumed = bad_requests / N(1−S)`; results are only emitted when
  counters share population definition, window and coverage — otherwise the
  assessment reports the mismatch.
- [x] Windowed history: `SloAssessment`s persisted per window with quality
  state and coverage attached.
- [x] Missing traffic, missing collection and service unavailability are
  reported as three distinct conditions (LAB-007: an incomplete source never
  yields fabricated health).
- [x] Reproducible evaluation: same input + same SLO revision → identical
  assessment under an injected clock.
- [x] Fixtures with mathematically known results cover counter resets (rates
  never go negative), sampled data (no global rates inferred from sampled
  traces — LAB-008) and low-traffic edge cases.
- [x] Indicators a source cannot supply are reported as absent — never
  interpolated.

# Constraints

- A missing source cannot produce fictitious positive health (invariant).
- Percentiles cannot be aggregated by simple average (invariant).
- Reset counters cannot produce negative rates (invariant).
- A non-sampled datum cannot be labeled sampled, nor the inverse (invariant).
- Altering a rule or SLO must not rewrite historical results without an
  explicit revision (invariant).
- SLO formulas are valid only under identical population definition, window
  and coverage; violations surface as explicit assessment limitations.
- Deterministic evaluation: no network, injected clock, typed results — the
  same replay yields the same assessment.
- Quality state stays a separate dimension, never folded into the operational
  conclusion (ADR-017/018).

# Review Notes

- Independently hand-check at least one fixture oracle (SLI, budget, burn
  inputs) — tests must prove math, not mirror the implementation.
- Verify sampled/partial coverage propagates into every emitted indicator.
- Check the three-way distinction (no traffic / no collection / down) is
  observable in API output, not just internal enums.
- Confirm counter-reset handling across cumulative temporality and process
  restarts.

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: multi-window multi-burn-rate alerting and notification rules
  (Cycle 4 consumes these indicators), Level-2 statistical baselines (Cycle
  4+), DORA delivery metrics.
- Riskiest assumption: eligible-request definitions stay consistent across
  heterogeneous sources for the same operation.
- Smallest acceptable version: request-count SLI/SLO + error budget over
  known-math fixtures, with reset and sampled-data oracles green.
