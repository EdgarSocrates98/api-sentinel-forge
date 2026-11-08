# ADR-007 — Deterministic Detector First

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Anomaly detection could be rule-based, statistical or LLM-driven. AI
must not be the first layer: detectors need reproducibility, explainable
math and deterministic tests from the MVP.

## Decision Drivers

- Detection must run offline with no model dependency.
- Every alert must explain the calculation that fired it.
- Invariants forbid fabricated health and unverifiable claims.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Three levels: deterministic rules → robust statistics → agentic investigation | L1 at MVP; L2 after reliable history; L3 interprets evidence, never invents anomalies | reproducible, testable, honest |
| B — LLM as primary detector | model flags anomalies directly | non-reproducible, costs tokens, risks fake causes |

## Decision

Deterministic rules are the detection engine's first layer, behind a
`Detector` protocol (typed results, injected clock, no network, no
credentials, no implicit persistence). Statistics arrive after
trustworthy history; agentic investigation is Level 3 only — hypotheses
follow `OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED`.

## Consequences

**Positive**

- Replay and detector-version evaluation come free from the protocol.
- Lab oracles can assert exact detection behavior (LAB-001…004).

**Negative**

- Novel anomaly shapes wait for L2/L3 maturity.
- Rule tuning (thresholds, cooldowns) is ongoing product work.

## Compatibility

Anomaly contract `api-sentinel/Anomaly/v1` carries `detector.id` +
`detector.version`, so consumers can replay and compare versions.

## Reversal Strategy

Adding L2 methods or changing L3 providers happens behind the Detector
protocol and anomaly schema — historical anomalies keep the detector
version that produced them; rule changes never silently rewrite
history (invariant).

## Links

- Blueprint `prompt_inicial.md` §10, §23 (ADR-007)
- `docs/08-slo-and-detection-design.md`
