# 14 — Performance & Cost Model

## Processing policy (proposed initial defaults — configurable, subject to benchmark)

| Parameter | Proposed default | Rationale |
| --- | --- | --- |
| Aggregation window | 1 minute | operational alignment |
| Short window | 5 minutes | fast detection |
| Medium window | 1 hour | trend |
| Long window | 24 hours | context |
| Tolerated lateness | 2 minutes | avoid dropping late events |
| Initial max batch | 10 MB | protect memory; benchmark pending |
| Ingestion concurrency | 2 local workers | start simple |
| Duplication policy | idempotent | safe re-execution |
| External input | disabled | secure by default |
| Schema failure | quarantine | never contaminate aggregates |

These are configuration hypotheses, not homologated limits.

Late events: the platform distinguishes **event time**, **ingest time**
and **processing time** explicitly. A window may reopen within a
controlled tolerance; after definitive close, a correction produces a
traceable revision — never a silent rewrite of a historical incident.

## Cost model

Even open-source and local-first, the Sentinel can generate indirect
observability costs through external connectors. A budgeting system
exists from the first external connector.

```
C_total = C_query + C_ingestion + C_storage + C_transfer + C_execution
```

Values vary by provider, region, account and retention policy.

## Per-source caps (every external source must support)

| Cap | Dimension |
| --- | --- |
| metrics per query | count |
| datapoints | count |
| lookback window | time range |
| collect frequency | interval |
| calls per cycle | count |
| bytes processed | volume |
| concurrency | workers |
| max runtime | duration |
| retries | count + backoff |
| stored volume | size |
| estimated financial budget | currency |

## Collection posture

No indiscriminate production-log scraping by default. Observe the
minimum needed; expand collection only during an authorized
investigation, when the added value is proportional.

## Throughput honesty

No events-per-second target is defined without benchmarks on the target
hardware. Small-import time is measured and documented rather than
constrained by an artificial threshold (see 13-e2e-test-strategy §
engineering targets).
