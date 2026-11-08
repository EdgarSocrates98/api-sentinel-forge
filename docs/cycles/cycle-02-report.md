# Cycle 2 — Report: Telemetry Ingestion & Quality

## Goal

Implement the five-stage ingestion pipeline
(`acquire → validate → normalize → deduplicate&assess → persist&aggregate`)
for the two MVP sources: normalized JSONL and a documented OTLP JSON subset.

## What landed

| Deliverable | Location |
| --- | --- |
| Ingestion bounded context (pure: injected clock/ids/redactor, no I/O) | `src/api_sentinel/ingestion/` |
| JSONL importer (`RuntimeObservationV1` per line) | `ingestion/importers.py::parse_jsonl` |
| OTLP JSON subset importer (`resourceMetrics/scopeMetrics`) | `ingestion/importers.py::parse_otlp_json` |
| Unit normalization (explicit units only, canonical `ms`) | `ingestion/normalize.py` |
| Quality assessment (worst-of declared∨detected) | `ingestion/quality.py` |
| Content idempotency keys (batch + observation) | `ingestion/dedup.py` |
| Bounded, redacted quarantine entries | `ingestion/quarantine.py` |
| 5-stage orchestrator (one transaction per batch) | `ingestion/pipeline.py` |
| `runtime_observations` + `ingestion_quarantine` tables | `migrations/0002_ingestion.sql` |
| `ObservationRepository` / `QuarantineRepository` ports + SQLite impls | `application/ports.py`, `infrastructure/storage/repositories.py` |
| `api-sentinel ingest --source-id ID --file PATH [--kind] --json` | `cli/__init__.py` |
| `api.runtime-observation` → **available** | `capabilities.py` |

## Semantics honored

- **Three clocks**: `window_start`/`window_end` (event time), `ingest_time`,
  `processing_time` — stored as distinct columns, never conflated.
- **Quality = worst(declared, detected)**: a declared `PARTIAL`/`SAMPLED` is
  never upgraded on our word; detected sampling (`sampling.rate<1` or OTLP
  dropped counts) marks `SAMPLED`; `window.end` older than
  `stale_after_seconds` (default 300 s) vs the injected clock marks `STALE`;
  unknown/missing units mark `INVALID`. OTLP carries no producer claim →
  `UNKNOWN`, honestly.
- **Kinds preserved**: counter/gauge/histogram stay typed; nothing reduces a
  histogram to a request counter and no code path averages percentiles.
- **Dedup**: batch key = sha256(source_id + content) → replay short-circuits
  (`skipped_duplicates = item_count`); per-observation content keys dedup
  partial overlaps across batches; a conflicting redelivery of an existing
  `observation_id` goes to quarantine as `conflicting_observation_id` — a
  traceable revision attempt, never a silent rewrite.
- **Quarantine**: stable machine-readable reasons (`invalid_json`,
  `not_an_object`, `unknown_schema`, `schema_validation`,
  `unresolved_service`, `origin_mismatch`, `undecodable_line`,
  `unsupported_metric`, `unsupported_shape`, `unsupported_datatype`,
  `malformed_datapoint`, `unresolved_service`, `conflicting_observation_id`),
  payload redacted + truncated at 4 KiB with `payload_truncated` flag.
- **Limits**: `max_batch_bytes` (10 MB) and `max_items` (100k) exceed →
  `ValidationError` bounded refusal, whole transaction rolled back.
- **Overlap**: observations from other sources covering the same
  service+window are reported (`overlapping_sources`, audit event, batch
  `transform_notes`); aggregates are grouped per source — never summed.
- **Identity**: `operation_id`/`service_id` are never synthesized (LAB-006);
  unknown services/origins are quarantined, not guessed.
- **Checkpoints**: line count (JSONL) / byte offset (OTLP) upserted per source
  after commit; interruption ⇒ whole transaction rolls back ⇒ replay is safe
  and dedup makes it idempotent.

## Documented OTLP subset

`resourceMetrics[] → scopeMetrics[] → metrics[]` limited to
`http.server.request.duration` (stable semconv) and `http.server.duration`
(legacy) histogram data points; attribute variants `http.request.method` /
`http.method`, `http.response.status_code` / `http.status_code`, `http.route`,
`url.scheme`, `server.address`/`http.host` are canonicalized and the variant
recorded under `attributes["otel.semconv"]` (`stable|legacy|mixed`) plus
`transform_notes`. Explicit-bounds `bucketCounts` convert to cumulative `le`
buckets; `aggregationTemporality` 1→`delta`, 2→`cumulative` is carried on the
measurement (cross-point delta/cumulative *conversion* requires state between
points and is intentionally deferred — the temporality is never collapsed).

## Evidence

```
$ .venv/Scripts/python.exe -m pytest -q
.................................................................        [100%]
65 passed in 1.98s

$ .venv/Scripts/python.exe -m ruff check .
All checks passed!

$ .venv/Scripts/python.exe -m mypy src
Success: no issues found in 36 source files
```

CLI smoke (LAB-001 fixture, replay proves LAB-009 idempotency):

```
$ api-sentinel ingest --source-id src_lab_jsonl --file lab/fixtures/lab-001-elevated-latency.jsonl --json
{"batch_id": "batch_01M6YN3KVPPE5SP95T6XNJ22KJ", "accepted": 8, "quarantined": 0, "skipped_duplicates": 0}
$ api-sentinel ingest --source-id src_lab_jsonl --file lab/fixtures/lab-001-elevated-latency.jsonl --json   # replay
{"batch_id": "batch_01M6YN3KVPPE5SP95T6XNJ22KJ", "accepted": 0, "quarantined": 0, "skipped_duplicates": 8}
$ api-sentinel ingest --source-id src_otlp --file otlp.json --json   # kind auto-detected
{"batch_id": "batch_01M6YN3W7CTK2495EP0ZNEJEXB", "accepted": 1, "quarantined": 1, "skipped_duplicates": 0}
```

Lab-oracle coverage: LAB-001 fixture 8/8 ingested; LAB-006 unmapped
route/operation stays `NULL`/`UNKNOWN`; LAB-009 replay ⇒ 0 duplicates,
identical aggregates; LAB-010 secrets in attributes/quarantine payloads are
redacted before persistence (`tests/security/test_ingestion_redact.py`).

## Open items / deferred

- Cumulative→delta (or reverse) temporality *conversion* is deferred — it
  needs cross-point state; temporality is carried explicitly instead.
- `ingest concurrency: 2 workers` (docs/05) — single-threaded MVP by design.
- Memory/execution-time limits are bounded via `max_batch_bytes`+`max_items`;
  there is no wall-clock watchdog on a single batch yet.
- Checkpoint is per-file granularity (line count / byte offset), not
  mid-file resume — an interrupted file re-ingests and dedup absorbs it.
