# 05 — Telemetry Contracts

## Ingestion pipeline (5 stages)

1. **Acquire** — read only authorized sources and scopes.
2. **Validate** — schema, size, version, origin.
3. **Normalize** — convert attributes/units without losing semantics.
4. **Deduplicate & Assess** — detect duplication, sampling, gaps.
5. **Persist & Aggregate** — record provenance, compute indicators.

## Signal types

| Type | Example | Handling |
| --- | --- | --- |
| Counter | total requests | differences, reset, temporality |
| Histogram | latency distribution | buckets + correct aggregation |
| Gauge | active requests | instant value per window |
| Span | one HTTP call | context and dependencies |
| Log | timeout/failure | redacted contextual event |
| Deployment | new version | change marker |
| Configuration | modified timeout | config event |
| Contract | versioned OpenAPI | reference for Doctor API |

## Fundamental metric rules

- Never average percentiles — aggregate compatible
  distributions/buckets, then compute quantiles.
- Never assume all 4xx are unavailability — error definition is
  configurable per service/operation; 401/403/429 get dedicated
  indicators.
- Never infer global error rate from sampled traces — use counters or
  adequate histograms.
- Never double-count events from different sources — the same flow can be
  partially measured by OTel instrumentation AND API Gateway metrics;
  both are identified, not summed.
- Gateway timeout ≠ application timeout — distinct failures on the same
  flow.

## OpenTelemetry compatibility

The adapter knows the difference between:

- `http.server.duration` and `http.server.request.duration`
- `http.method` and `http.request.method`
- `http.status_code` and `http.response.status_code`
- milliseconds and seconds
- cumulative and delta temporality
- explicit and exponential histograms
- resource, scope and measurement-point attributes

A schema/convention-version identifier rides every transformation.

## Sources by phase

| Source | MVP | Post-MVP | Notes |
| --- | --- | --- | --- |
| Normalized JSONL | yes | yes | simplest lab input |
| OTLP JSON export | yes | yes | bounded, validated |
| OTel Collector | optional | yes | never rebuilt |
| Prometheus HTTP API | no | yes | allowed queries, limits, auth |
| CloudWatch Metrics | no | yes | paged queries + budgets |
| API Gateway | no | yes | metrics, logs, context |
| CloudWatch Logs | no | yes | redacted, filtered |
| AWS X-Ray | no | yes | traces when available |
| Azure Monitor | no | yes | isolated adapter |
| Datadog/Dynatrace | no | future | never replicate their platforms |
| GitHub Actions/CI | exported file | yes | deployment events |
| Kubernetes | no | future | workloads and changes |

## Processing policy (initial, configurable)

| Parameter | Default | Rationale |
| --- | --- | --- |
| Aggregation window | 1 min | operational alignment |
| Short window | 5 min | fast detection |
| Medium window | 1 h | trend |
| Long window | 24 h | context |
| Tolerated lateness | 2 min | keep late events |
| Max batch | 10 MB | memory protection, subject to benchmark |
| Ingest concurrency | 2 local workers | start simple |
| Duplication policy | idempotent | safe re-run |
| External input | disabled | secure by default |
| Schema failure | quarantine | no aggregate contamination |

Event time, ingest time and processing time are distinct. A window may
reopen within a controlled tolerance; after final close, a correction is a
tracked revision — never a silent rewrite of a historical incident.
