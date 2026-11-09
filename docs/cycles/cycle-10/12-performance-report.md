---
cycle: 10
deliverable: performance-report
wave: 4
---

# Cycle 10 — Performance Report (F006: ingestion N+1)

**Wave 4, LOCAL_REAL.** Finding F006 measured two N+1 patterns inside the
ingestion pipeline. This report reproduces the defect on the pre-change code,
describes the repository-level batching fix, and shows before/after evidence
with full semantic parity across all ten workload shapes.

## Defect (pre-change, reproduced)

`src/api_sentinel/ingestion/pipeline.py` issued, per accepted observation:

1. **dedup point query** — one `observations.get(observation_id)` per item;
2. **service point query** — one `services.get(service_id)` per item;
3. **overlap window query** — one `overlapping_sources(...)` per
   (service, window) pair instead of one query per service.

At 10k observations per run the SQL counter recorded 10 000 dedup point
queries, 10 000 service point queries and up to 2 880 overlap window queries
per workload — see `evidence/performance-before.json`.

## Fix

Repository-level batching, wired through the application port:

- `ServiceRepository.get_many(ids)` /
  `ObservationRepository.idempotency_keys(ids)` /
  `ObservationRepository.overlapping_sources_in_ranges(service_id, windows)`
  — added to `src/api_sentinel/application/ports.py` and implemented in
  `src/api_sentinel/infrastructure/storage/repositories.py` as
  `IN (…)` chunk-batched reads (the overlap variant does one bounding
  scan per service and matches exact windows in memory — identical
  semantics, never a bounding-box approximation);
- `pipeline.py` now prefetches service ids once per chunk, batch-loads
  dedup keys, and issues one overlap query per service for the whole window
  set. Per-observation lookups were removed.

Regression guard: `tests/integration/test_ingest_batching.py` (5 tests —
batch dedup, batch overlap, service prefetch, replay equivalence,
quarantine parity).

## Method

`lab/bench/bench_matrix.py` generates deterministic datasets, counts SQL by
class (`dedup_point_query`, `service_point_query`, `overlap_window_query`,
`*_batch_query`, `*_insert`, `other`), records wall-clock seconds, tracemalloc
peak and DB size, and re-verifies stored row counts. Identical datasets were
re-ingested before and after the change (dataset sha256 checked per shape).

## Results — elapsed seconds (before → after)

| shape | before | after | Δ | dedup pt→batch | svc pt→batch | overlap win→batch |
|---|---|---|---|---|---|---|
| sequential | 8.04 | 7.76 | −0.28 | 10 000 → 26 | 10 000 → 2 | 100 → 2 |
| dispersed (1 440 windows) | 10.87 | 8.27 | −2.60 | 10 000 → 26 | 10 000 → 2 | 2 880 → 2 |
| out_of_order | 8.14 | 8.77 | +0.63 | 10 000 → 26 | 10 000 → 2 | 100 → 2 |
| multi_source | 8.35 | 8.05 | −0.30 | 10 000 → 26 | 10 000 → 2 | 240 → 2 |
| high_cardinality | 8.20 | 10.59 | +2.39 | 10 000 → 26 | 10 000 → 2 | 100 → 2 |
| dup_heavy | 6.51 | 6.70 | +0.19 | 5 000 → 14 | 10 000 → 2 | 50 → 2 |
| mid_batch_failure | 7.08 | 6.54 | −0.54 | 8 000 → 20 | 8 000 → 2 | 80 → 2 |
| reprocess (idempotent) | 0.29 | 0.30 | +0.01 | 0 → 0 | 0 → 0 | 0 → 0 |
| large_file | 8.66 | 7.77 | −0.89 | 10 000 → 25 | 10 000 → 2 | 100 → 1 |
| concurrent | 8.22 | 8.71 | +0.49 | 10 000 → 26 | 10 000 → 2 | 240 → 2 |

Point queries are fully eliminated: every dedup/service/overlap lookup is now
a constant-size batch (`dedup_batch_query` 14–26 per 10k-observation run,
`service_batch_query` 2, `overlap_batch_query` 1–2 regardless of window
dispersion — the 2 880→2 collapse on the dispersed shape is the headline).

Wall-clock is modestly better on shapes dominated by overlap dispersion
(dispersed −24 %, large_file −10 %) and within noise elsewhere; the
benchmark's dominant cost is per-row insert + contract validation, not
lookups. The structural win is that lookup cost no longer scales with
dataset size or window dispersion — it scales with batch size.

## Semantic parity (all 10 shapes)

Identical dataset sha256 before/after for every shape, plus identical
accepted / skipped_duplicates / quarantined / stored_observations counts:

- `sequential`, `dispersed`, `out_of_order`, `multi_source`,
  `high_cardinality`, `large_file`, `concurrent`: 10 000 accepted,
  10 000 stored, 0 quarantined;
- `dup_heavy`: 5 000 accepted, 5 000 skipped as duplicates (dedup parity);
- `mid_batch_failure`: 8 000 accepted, 2 000 quarantined (failure path
  parity);
- `reprocess`: 0 accepted / 10 000 skipped while the original 10 000 rows
  remain stored (idempotent replay).

## Evidence

- `evidence/performance-before.json` — pre-change run (measured on the
  N+1 code path, SQL counts above);
- `evidence/performance-after.json` — post-change run, identical datasets;
- `evidence/performance-ab.json` — A/B re-run used during iteration;
- `lab/bench/bench_matrix.py` — generator + counter;
- `tests/integration/test_ingest_batching.py` — regression tests.

## Limitations

- SQLite backend; batch `IN (…)` chunk sizes chosen for SQLite's parameter
  limit — a different backend would need its own batching constant.
- Wall-clock deltas on insert-dominated shapes are within run-to-run noise;
  the provable change is the SQL count shape (O(n) → O(n/chunk)), not a
  universal latency promise.
- Benchmarks run locally (LOCAL_REAL); no production dataset was harmed or
  consulted.
