# Cycle 3 — Report: Runtime Health, SLI/SLO and Error Budgets

## Goal

Build the `metrics` and `reliability` bounded contexts on top of Cycle-2
normalized observations: windowed request/latency/error aggregations,
versioned SLI/SLO definitions, `B = N(1-S)` error budgets, windowed
assessment history, and honest per-service health — governed by
`docs/08-slo-and-detection-design.md` and `docs/05-telemetry-contracts.md`.

## Acceptance criteria

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Request aggregations per service/operation per window: request rate, error ratio, timeout ratio over eligible requests | PASS | `metrics/aggregates.py::compute_window_aggregates` keyed by `(service, operation, source)`; `test_delta_counters_sum_to_eligible`, `test_mixed_classification_aggregation` |
| 2 | Latency quantiles (p50/p95/p99) only over compatible distributions — percentiles never averaged | PASS | `merge_bucket_sets` requires identical `le` bounds (no rebucketing) then `quantile` interpolates; `test_percentile_from_merged_buckets_oracle`, `test_incompatible_bounds_never_rebucketed` |
| 3 | Availability SLI `good/eligible` and latency SLI `within threshold/eligible` per docs/08; classification varies per SLO/service | PASS | `reliability/sli.py`, `evaluate_slo` + `cumulative_at` over merged buckets; `test_availability_assessment_oracle_met`, `test_latency_sli_oracle` |
| 4 | Error classification configurable per service/operation; not blanket 4xx; 401/403/429 dedicated indicators | PASS | `ErrorClassification` (default `5xx`+timeout); `auth_denied_requests`/`rate_limited_requests` fields; `--error-status` CLI flag; `test_dedicated_indicators_counted_not_bad`, `test_classification_is_configurable` |
| 5 | `SloDefinition` versioned (`slo_id`+revision, S, window); change => new revision; LAB-015 revision preserved + reproducible | PASS | `UNIQUE(slo_id, revision)` table; conflicting re-define => `ConflictError`, identical => no-op; `test_slo_revision_preserved_and_reproducible` |
| 6 | `B = N(1-S)`, `consumed = bad / N(1-S)`; emitted only under shared population/window/coverage, else mismatch reported | PASS | `reliability/budget.py` verbatim formulas; sources never summed (`per_source` + `multiple_sources_not_summed`); `INCOMPATIBLE_INPUTS` when no request plane; `test_error_budget_oracle`, `test_multiple_sources_identified_not_summed` |
| 7 | Windowed history: assessments persisted per window with quality state and coverage | PASS | `migrations/0003_reliability.sql` `slo_assessments`; `SloAssessment.quality` + `SloCoverage` (included/excluded/reasons); `test_slo_persists_and_rehydrates` |
| 8 | Missing traffic / missing collection / service unavailability = three distinct conditions (LAB-007) | PASS | `HealthCondition`: `no_traffic` / `source_stale` / `failing` (+ `observed`/`degraded`/`unmeasurable`/`no_observations`) surfaced in `health --json`; `test_health_*` |
| 9 | Reproducible evaluation: same input + same revision => identical assessment under injected clock | PASS | deterministic `assessment_id_for`, pure `evaluate_slo`, `ON CONFLICT … DO UPDATE` replace; `test_evaluation_is_reproducible`, `test_reevaluation_same_slot_is_idempotent` |
| 10 | Known-math fixtures: counter resets (never negative), sampled data (LAB-008), low-traffic edges | PASS | reset oracle `100→150→40→90 = 140`; sampled/invalid excluded; zero-traffic → `no_traffic`; `test_cumulative_counter_reset_never_negative`, `test_sampled_and_invalid_never_feed_global_rates` |
| 11 | Indicators a source cannot supply reported absent — never interpolated | PASS | `absent_indicators` on health report, `None` indicators on `WindowAggregate`, `INSUFFICIENT_DATA` SLO state; `test_health_absent_indicators_reported` |

## What landed

| Deliverable | Location |
| --- | --- |
| Metrics context (pure: Protocol-typed observations, no I/O, no clock) | `src/api_sentinel/metrics/` |
| `TimeWindow` (containment semantics), duration parser | `metrics/windows.py` |
| Error classification + counter deltas + histogram merge/quantile + `WindowAggregate` | `metrics/aggregates.py` |
| Reliability context (pure: domain + metrics imports only) | `src/api_sentinel/reliability/` |
| `SLI = good/eligible` (None when eligible=0) | `reliability/sli.py` |
| `SloDefinition`/`SloAssessment`/evaluation states/`evaluate_slo` | `reliability/slo.py` |
| `B = N(1-S)`, consumed, remaining | `reliability/budget.py` |
| Health semantics `AVAILABLE\|UNAVAILABLE\|PARTIAL\|UNVERIFIED\|UNKNOWN` + conditions | `reliability/health.py` |
| `slo_definitions` + `slo_assessments` tables | `migrations/0003_reliability.sql` |
| `SloDefinitionRepository`/`SloAssessmentRepository` + `list_for_service` | `application/ports.py`, `infrastructure/storage/repositories.py` |
| `DefineSlo`/`EvaluateSlo`/`ListSloDefinitions`/`GetServiceHealth` use cases | `application/use_cases.py` |
| `api-sentinel slo define\|evaluate\|list`, `api-sentinel health` | `cli/__init__.py` |
| `api.slo-assessment` → **available** | `capabilities.py` |
| `worst_of`/`worst_quality` moved to `domain.quality` (shared severity order) | `domain/quality.py` |

## Semantics honored

- **Percentiles**: cumulative buckets merged only over identical bound sets,
  then linear interpolation inside the containing bucket (Prometheus-style;
  lower edge of first bucket = 0 for durations ≥ 0; `+Inf` cannot interpolate
  upward and returns the last finite bound). Incompatible bounds → the
  indicator is absent, never rebucketed. Non-monotonic "cumulative" input
  whose non-terminal sum equals `+Inf` is reinterpreted as per-bucket counts
  and *recorded* (`bucket_semantics:per-bucket->cumulative` limitation) —
  the lab fixture needs exactly this (obs_0001–05 are per-bucket-consistent,
  obs_0006–08 are cumulative-consistent; disambiguation is deterministic).
- **Counters**: delta temporality sums directly; cumulative is differenced
  per series (pinned by status/error/metric-name labels) — negative delta ⇒
  reset ⇒ delta = post-reset reading, rates never negative. A series' first
  reading yields no delta (`counter_baseline_missing`, recorded).
- **Sampling**: `SAMPLED`/`INVALID` observations never feed global rates —
  excluded with counts under `excluded_by_reason` + limitation (LAB-008);
  sampled-only windows → `INSUFFICIENT_DATA`/`UNMEASURABLE`, never healthy.
- **Population**: aggregation groups are pinned by `source_id`; multiple
  sources are reported per-source with a deterministic primary pick
  (largest eligible, then source_id) — identified, never summed. When no
  consistent request population exists → `INCOMPATIBLE_INPUTS`.
- **Request plane**: histogram (duration, canonical ms) > counter
  (`requests`/`count`) > summary; mixing is reported, the richer plane is
  authoritative — never double-counted.
- **Missing data** (LAB-007): `no_traffic` ≠ `source_stale` ≠ `failing`.
  UNAVAILABLE requires fresh error-ratio evidence at the threshold; idle
  (`eligible == 0` on fresh request data) and silent/stale sources are
  `UNVERIFIED`; nothing-ever-observed is `UNKNOWN`.
- **Quality ≠ conclusion** (ADR-017/018): `state` carries the SLO/health
  outcome, `quality` carries the worst input quality — e.g. the lab e2e
  returns `state: MET` with `quality: STALE`.
- **Versioning**: SLO revisions immutable (`UNIQUE(slo_id, revision)`;
  conflicting re-define ⇒ `sentinel.conflict`); assessments carry
  `rule_id=slo.evaluate`/`rule_version=1` + the SLO revision — historical
  results are never rewritten.
- **Idempotency**: `assessment_id` is a deterministic hash of
  `(slo_id, revision, window)`; `ON CONFLICT … DO UPDATE` replaces the row —
  re-evaluation is a no-op, not a duplicate.

## Evidence

```
$ .venv/Scripts/python.exe -m pytest -q
........................................................................ [ 61%]
..............................................                           [100%]
118 passed in 2.75s

$ .venv/Scripts/python.exe -m ruff check .
All checks passed!

$ .venv/Scripts/python.exe -m mypy src
Success: no issues found in 44 source files
```

CLI smoke (lab fixture remapped to generated ids; real clock → honest states):

```
$ api-sentinel ingest --source-id src_… --file lab-remapped.jsonl --json
{"batch_id": "batch_…", "accepted": 8, "quarantined": 0, "skipped_duplicates": 0}

$ api-sentinel slo define --service-id svc_… --target 0.99 --window 1h --slo-id slo_smoke --json
{"slo_id": "slo_smoke", "revision": 1, "sli_kind": "availability", "target": 0.99, "window_seconds": 3600, …}

$ api-sentinel slo evaluate --slo-id slo_smoke --window-start 2026-01-01T00:00:00+00:00 --window-end 2026-01-01T00:08:00+00:00 --json
{"assessment_id": "asmt_a5102efba372ebed3cebdc7e", "sli": 1.0, "eligible": 951.0,
 "good": 951.0, "bad": 0.0, "budget_total": 9.51, "budget_consumed": 0.0,
 "state": "MET", "quality": "STALE", "rule_id": "slo.evaluate", "rule_version": "1",
 "primary_source_id": "src_…",
 "limitations": ["bucket_semantics:per-bucket->cumulative"],
 "coverage": {"sources_used": ["src_…"], "observations_included": 8,
              "observations_excluded": 0, "window_seconds": 480.0}}

$ api-sentinel health --service-id svc_… --json
{"state": "UNVERIFIED", "condition": "source_stale", "quality": "STALE",
 "latest_window_end": "2026-01-01T00:08:00Z",
 "unknowns": ["source_stale:src_…"], "absent_indicators": ["request_rate", …]}
```

Lab-oracle coverage: LAB-007 three-way missing-data states observable in
`health --json`; LAB-008 sampled rows excluded from all rate math; LAB-015
revision 1 preserved and re-evaluates identically after revision 2 exists.

## Open items / deferred

- **Cumulative histogram `sum` mean** is reported absent under cumulative
  temporality (`latency_mean_unavailable:cumulative_temporality`) — sums are
  not differenced, only buckets/counts.
- **Burn-rate alerting** (multi-window multi-burn-rate), baselines and
  detector protocol are Cycle-4 scope by the spec's grill gate.
- **Multiple sources** choose a deterministic primary rather than summing —
  a federation story (per-source SLIs are all reported) is future work.
- **Cumulative counter series without an in-window predecessor** report
  `counter_baseline_missing` and count only from the first delta — honest
  undercount rather than guessing pre-window history.
- Error classification for health currently uses the documented default
  (`5xx`+timeout); per-service overrides ride SLO definitions — a shared
  service-level classification profile is a natural follow-up.
