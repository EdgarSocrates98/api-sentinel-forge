# ADR-005 — External OpenTelemetry Collector (Optional)

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Continuous collection needs receivers, processors, exporters,
connectors and extensions. The OpenTelemetry Collector already provides
this pipeline officially; rebuilding it inside the Sentinel duplicates a
mature component.

## Decision Drivers

- MVP ingestion starts from normalized JSONL and validated OTLP JSON
  exports — files, not live pipelines.
- Continuous pipelines, when needed, should reuse the existing
  collector rather than reinvent receivers/exporters.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Optional integration with external OTel Collector | Sentinel ingests its exports; collector is an opt-in source | reuses proven pipeline; keeps core small |
| B — Rebuild collector functionality inside Sentinel | embedded receivers/processors | large duplicated surface, config compatibility burden |

## Decision

Treat the OpenTelemetry Collector as an optional external integration.
Sentinel owns semantic normalization and quality assessment of what it
receives — not the collection pipeline itself.

## Consequences

**Positive**

- No receiver/exporter zoo to maintain.
- The Collector profile in the install wizard configures an existing
  tool instead of shipping a fork.

**Negative**

- Continuous collection depends on an external process the user runs.
- Collector-side misconfiguration appears as Sentinel data quality —
  must be surfaced via source state, not hidden.

## Compatibility

OTLP JSON ingestion contract stays the same regardless of the source;
semconv-version handling is explicit in the adapter (docs/16 OTel
migration table).

## Reversal Strategy

If an embedded collector becomes necessary, it lands as a new bounded
integration module behind the same ingestion port — stored data and
contracts unchanged. Trigger: demonstrated need for Sentinel-managed
pipelines.

## Links

- Blueprint `prompt_inicial.md` §4, §7.3–7.4, §23 (ADR-005)
- `docs/16-compatibility-matrix.md`
