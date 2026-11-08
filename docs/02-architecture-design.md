# 02 — Architecture Design (ADD)

## Style

Modular monolith + hexagonal architecture, with persisted internal events
for relevant state changes. No microservices in the MVP: boundaries exist
in code from day one, deployment starts as a single local CLI/FastAPI
process (ADR-002).

## Three planes

- **Data Plane**: acquisition, validation, transformation and storage of
  observations (ingestion, telemetry normalization, persistence).
- **Intelligence Plane**: indicators, SLOs, detection, correlation,
  hypotheses and incidents (metrics, reliability, detection, correlation,
  incidents).
- **Control & Experience Plane**: governance, specialist integration,
  public APIs and Forge Portal (api, cli, operations, integrations).

## Bounded contexts

| Context | Exclusive responsibility |
| --- | --- |
| `inventory` | service/environment/operation/release identity |
| `ingestion` | receiving observations |
| `telemetry` | normalization and data quality |
| `metrics` | aggregations and indicators |
| `reliability` | SLI, SLO, error budget |
| `detection` | rules, baselines, anomalies |
| `correlation` | temporal relations and hypotheses |
| `incidents` | incident lifecycle |
| `evidence` | integrity, provenance, retention |
| `integrations` | contract adapters between Forges |
| `api` | local HTTP surface |
| `cli` | terminal surface |
| `operations` | scheduling of opt-in collectors |

Load-bearing rule: `detection` never knows AWS; `incidents` never knows
Angular; `inventory` never depends on Code Atlas internals.

## Internal module rules

- One writer per table; cross-context references by id only.
- Cross-module communication via ports + facades/events — no deep imports.
- Domain/application layers are deterministic: injected clock/id, no
  network, no credentials.
- External systems sit behind Anti-Corruption Layer adapters in
  `integrations/` (translating foreign models at the boundary).

## Deployment topology

Single local process. Optional `api` extra exposes a local HTTP surface
for the Portal. Opt-in collectors run as governed jobs. Storage: SQLite
(WAL) + content-addressed evidence files; Parquet/DuckDB optional for
analytical history (ADR-004, ADR-005).

## Failure strategy

The Sentinel is never a global point of failure:

- Doctor down → metrics/incidents still shown; specialized diagnosis
  recorded as not-concluded.
- Atlas down → investigation continues without verified structural
  dependencies.
- Portal down → CLI and local API keep working.
- Bridge down → deterministic investigations stay available.
- Telemetry source down → show missing data, never fabricated health.

## Evolution path

Stage 1 (now): one deploy, one db, clear boundaries.
Later: promote a module to its own process/db only when its own metrics
justify it; contracts are versioned so extraction doesn't re-break
consumers.
