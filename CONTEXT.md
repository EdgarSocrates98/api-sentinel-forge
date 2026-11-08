# CONTEXT — shared language for API Sentinel Forge

One term per line: what it means in *this* project. Grouped by bounded
context. If a term means two things, it is two terms — find the boundary.

## inventory

- `ApiService` — a logical API service being observed; stable identity,
  not a process. `service_id`.
- `ServiceEnvironment` — a logical operating scope of a service
  (production/staging/dev + region/cluster/account). `environment_id`.
- `ApiOperation` — one callable surface of a service: an HTTP method+route
  template, a GraphQL operation, or an RPC method. `operation_id`.
- `ServiceRelease` — a deployed version of a service; does NOT compose the
  logical identity but segments investigations. `release_id`.
- `namespace` — coarse grouping of services (e.g., `payments`).
- `external_refs` — links from an identity to outside systems
  (cloud resource, Code Atlas entity, OpenAPI operationId); each ref
  carries provenance. Two different `payments-api` in different accounts
  are two different services.

## telemetry / ingestion

- `TelemetrySource` — an authorized origin of observations (OTel
  collector, JSONL file, CloudWatch query…). `source_id`.
- `ObservationBatch` — one received ingest unit; carries idempotency key,
  schema version and source ref. `batch_id`.
- `RuntimeObservation` — one normalized measurement over a window
  (counter/gauge/histogram/span/log/deployment/config/contract).
- `MetricSeries` — a metric identity pinned by dimensions.
  `MetricPoint` — a value or distribution at a time within a series.
- `acquire → validate → normalize → deduplicate&assess → persist&aggregate` —
  the five ingestion stages; each stage is a separate responsibility.
- `event_time` / `ingest_time` / `processing_time` — three distinct
  clocks attached to an observation; never conflated.
- `lateness` — tolerated event-time lag before a window closes;
  corrections after closure are revisions, not silent rewrites.
- `quarantine` — payload that failed schema validation; held apart so it
  cannot contaminate aggregates.

## reliability / metrics

- `SLI` — service-level indicator (e.g., good requests / eligible
  requests).
- `SLO` — objective over an SLI (`S`, e.g. 0.999) with a window and a
  revision; `slo_id` + `revision` identify it.
- `eligible requests` — the population a given SLI is defined over;
  configured per service/operation, not universal.
- `error budget` — `B = N(1-S)`, requests allowed to fail inside the window.
- `burn rate` — speed of budget consumption relative to the allowed rate;
  drives alerts multi-window (short+long) with hysteresis and cooldown.
- `coverage` — completeness/representativeness of the measurement itself.
- `traffic baseline` — comparison of current volume vs compatible periods.

## detection

- `DetectionRule` — a versioned deterministic rule; `rule_id` + `revision`.
- `Detector` — pure evaluator: `evaluate(request, context) → DetectionResult`;
  no network, no real clock, no credentials, no implicit persistence.
- `Anomaly` — a detected deviation with status ladder
  `OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED`. `anomaly_id`.
- `baseline` — expected behavior learned from history (median/MAD, EWMA,
  hour-of-day…); a Level-2 input, never present in Level-1 rules.

## incidents

- `Incident` — an operational investigation object: state + journal.
  `incident_id`.
- `incident_event` — an append-only journal entry for every state change;
  a closed incident is never edited, only appended.
- `severity` — SEV1..SEV4 + INFORMATIONAL, driven by business impact,
  not by HTTP status codes.
- `hypothesis` — a candidate cause with explicit confidence class
  (`PROBABLE`, `POSSIBLE`, `UNVERIFIED`…), distinct from workflow state.
- `deduplication` — grouping of anomalies into one incident on
  service+operation, window, detector signature, shared dependency,
  environment, release; originals preserved with justification.

## evidence

- `EvidenceArtifact` — a verifiable reference to evidence: hash, source,
  retention link, size bounds. `evidence_id`.
- `provenance` — who produced a datum, when, from what input, transformed
  by which adapter version.
- `quality state` — `COMPLETE | PARTIAL | SAMPLED | STALE | INVALID | UNKNOWN`,
  orthogonal to the operational conclusion.

## correlation / change

- `ChangeEvent` — deploy/config/flag/contract/dependency/maintenance
  change. `change_id`.
- `DependencyObservation` — an *observed* call edge between services
  (operational graph, Sentinel-owned); NOT the structural graph.
- `structural edge` — a relation found in code/contracts/IaC
  (Code-Atlas-owned). A structural edge does not prove a call happened;
  an observed call does not prove a code path.
- `Investigation` — one execution of the investigative process.
  `investigation_id`.
- `ExternalHandoff` — a governed delegation to a specialist Forge
  (Doctor, Atlas, Data Sentinel…) with a receipt. `handoff_id`.

## platform

- `workspace` — local state root: `.api-sentinel/` (project scope) or
  `~/.api-sentinel/` (user scope); holds `sentinel.db`, config, receipts.
- `forge.json` — specialist manifest (ids, cli, extras, install hooks).
- `forge.agentic.json` — `forge/SpecialistAgenticManifest/v1`, the
  workflows/skills/agents catalog for hosts.
- `capability` — a declared ability surfaced to The Forge via runtime
  discovery; declared ≠ available, and DISCOVERED ≠ VERIFIED.
- `handoff boundary` — the public seam another Forge's adapter drives;
  the Sentinel's own boundary exposes typed seams, never internals.

## Hard calls (decision records in docs/adr/)

- Logical identity excludes the deployment version (ADR-012).
- Incident store = current-state tables + journal, not full event sourcing (ADR-008).
- Quality state is a separate dimension, never folded into health (ADR-017/018).
- SQLite first; Parquet/DuckDB optional for analytical history (ADR-004).
