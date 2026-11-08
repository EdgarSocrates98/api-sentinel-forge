# ADR-014 — Metadata Collection by Default

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Logs, traces and payloads can embed credentials and PII. The sensitive-
data policy must be safe before the first byte is stored, not opt-in
hardening later.

## Decision Drivers

- "Credentials or PII in evidence" is a CRITICAL risk (§27).
- Minimization is cheaper at ingest than at purge time; redaction
  before write is the policy.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Metadata-first: headers/bodies off by default; sensitive ids transformed; URL query values not persisted; redact before write and again at presentation | safe floor; depth added only when authorized | prevents the worst leak class by default |
| B — Automatic payload capture | full-fidelity telemetry by default | persistent credential/PII surface; violates minimization |

## Decision

Collect metadata first. Payload-level capture is explicit, scoped and
authorized — typically during a bounded investigation, when value is
proportional (§22 posture).

## Consequences

**Positive**

- LAB-010 oracle ("no known secret persisted") is achievable.
- Evidence-access audit stays meaningful — less sensitive material to
  protect.

**Negative**

- Deep investigations may need an extra authorized collection pass.
- Redaction quality becomes a security-critical component to test.

## Compatibility

Observation/evidence contracts carry quality + limitation fields so
consumers know when payload context was deliberately not captured —
absence is declared, not silent.

## Reversal Strategy

Enabling richer capture is configuration + authorization, not a schema
break: new evidence simply carries more fields within size bounds.
Tightening again means purging captured payloads per the explicit
purge policy — which exists because of this ADR.

## Links

- Blueprint `prompt_inicial.md` §16.4, §22, §23 (ADR-014)
- `docs/07-security-threat-model.md`, `docs/10-storage-and-retention.md`
