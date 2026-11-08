---
id: cycle-02-telemetry-ingestion
title: "Cycle 2 — Telemetry Ingestion & Quality"
agent: claude
risk: medium
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Implements the five-stage ingestion pipeline (`acquire → validate → normalize →
deduplicate&assess → persist&aggregate`) for the two MVP sources: normalized
JSONL and a supported subset of OTLP JSON. Governed by
`docs/05-telemetry-contracts.md` (stages, signal types, metric rules),
`docs/03-domain-model.md` (`TelemetrySource`, `ObservationBatch`,
`RuntimeObservation`, `MetricSeries`/`MetricPoint`, quality state), and
`docs/02-architecture-design.md` (bounded contexts `ingestion` vs `telemetry`;
blueprint §7).

_Depends: Cycle 1 — Core, Identity & Persistence (predecessor cycle, not in
this inbox): repositories, migrations, telemetry-source contract, workspace._

# Acceptance Criteria

- [x] `api-sentinel ingest <file>.jsonl` imports a normalized JSONL batch
  end-to-end into SQLite with recorded provenance (`source_id`,
  `adapter_version`, `batch_id`).
- [x] A documented OTLP JSON subset is accepted; unsupported shapes and unknown
  schema versions are rejected explicitly (never silently defaulted) and routed
  to quarantine.
- [x] Unit normalization is lossless: ms/seconds and cumulative/delta
  temporality are converted with explicit units carried on the observation;
  the normalization fixture corpus shows zero unit distortion.
- [x] Counter, gauge and histogram produce distinct typed
  `RuntimeObservation`s; an HTTP histogram is never reduced to a synthetic
  request counter.
- [x] `event_time`, `ingest_time` and `processing_time` are stored as three
  distinct clocks; aggregation windows are configurable (default 1 min) with
  tolerated lateness (default 2 min); corrections after window closure are
  recorded as traceable revisions, not silent rewrites.
- [x] Deduplication: `ObservationBatch` carries an idempotency key;
  reprocessing the same batch produces zero duplicate points (LAB-009 oracle).
- [x] Every observation carries a quality state
  (`COMPLETE | PARTIAL | SAMPLED | STALE | INVALID | UNKNOWN`); sampled data is
  labeled sampled and never the inverse.
- [x] Quarantine: payloads failing schema/size/version/origin validation are
  held apart, queryable, and cannot contaminate aggregates.
- [x] Ingestion checkpoints are recorded per source; an interrupted import
  resumes without duplicating already-committed points.
- [x] Configurable limits for batch size (initial default ~10 MB), memory and
  execution time; exceeded limits produce a bounded refusal or truncation —
  and truncated evidence records its loss.
- [x] Everything persisted passes through redaction first; the LAB-010
  credential fixture proves no known secret is persisted.
- [x] An unmapped route keeps its identity `UNKNOWN` (LAB-006): no fabricated
  service/operation identity is synthesized during normalization.
- [x] Overlapping sources (e.g., OTel instrumentation + gateway metrics
  measuring the same flow) are identified as partial overlap, never summed
  indiscriminately.

# Constraints

- Deterministic core: `domain`/`application` perform no network I/O, read no
  wall clock (injected `clock`), touch no credentials.
- An idempotent batch never duplicates points (invariant).
- A non-sampled datum can never be labeled sampled, nor the inverse
  (invariant).
- A missing source cannot produce fictitious positive health — absence is
  recorded, not filled (invariant).
- Truncated evidence must indicate loss (invariant).
- An unknown contract version cannot be accepted silently (invariant);
  envelopes are `api-sentinel/<Name>/v1`.
- Secrets never appear in receipts or logs; all persisted payloads cross
  `infrastructure/security/redact.py`.
- External input is disabled by default; the acquire stage reads only
  authorized sources and scopes.
- OTel semantic-convention differences (`http.server.duration` vs
  `http.server.request.duration`, attribute renames, explicit vs exponential
  histograms) are handled through documented mappings, with the convention
  version attached to the transform.
- Cross-source duplication is assessed, not silently merged.

# Review Notes

- Boundary respect: `ingestion` only receives; `telemetry` owns
  normalization/quality — check no context does the other's job (docs/02).
- Honest states: `PARTIAL`/`STALE`/`SAMPLED` never surface as `COMPLETE`, and
  no code path interpolates absent data.
- Verify dedup is content/key-based and survives restart (checkpoint +
  idempotency key), not just in-process replay.
- Check quarantine is inspectable and bounded, not a silent drop.
- Confirm the three clocks are never conflated in windowing code.

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: running an external OTel Collector pipeline, Prometheus /
  CloudWatch / Azure / Datadog connectors (post-MVP per §7.4), analytical
  store (Parquet/DuckDB remain optional), streaming ingestion.
- Riskiest assumption: the documented OTLP JSON subset covers the lab corpus
  without needing a full collector beside the repo.
- Smallest acceptable version: JSONL import + idempotent dedup + quarantine +
  checkpoints, passing LAB-006/LAB-009/LAB-010 oracles on fixtures.
