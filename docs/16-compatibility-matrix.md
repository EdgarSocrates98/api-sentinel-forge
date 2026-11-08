# 16 — Compatibility Matrix

The product distinguishes what it can **observe** from what it can
**analyze semantically**.

## API technologies

| Technology | MVP | Evolution |
| --- | --- | --- |
| REST/HTTP + OpenAPI 3.0/3.1 | telemetry + contract references | operational drift and impact |
| OpenAPI 3.2 | version recognition + support diagnosis | full integration after library validation |
| GraphQL | generic HTTP observation | operation identity + GraphQL errors |
| gRPC | generic observation where the source allows | RPC methods, status, streaming |
| AsyncAPI | import as context, if supported | consumers, publishers, channels, backlog |
| WebSockets | out of initial core | sessions, disconnects, latency |
| Arazzo | out of MVP | business-workflow monitoring |

Arazzo 1.1.0 already describes workflows spanning HTTP and event-driven
APIs (useful for order → payment → notification investigations), but
does not justify a workflow executor in the first cycle.

## OpenTelemetry HTTP semantic-convention migration

OTel evolved its HTTP conventions: explicit converters are required —
not field renames.

| Aspect | Legacy | Current |
| --- | --- | --- |
| server duration metric | `http.server.duration` | `http.server.request.duration` |
| method attribute | `http.method` | `http.request.method` |
| status attribute | `http.status_code` | `http.response.status_code` |
| duration unit | milliseconds | seconds |
| temporality | cumulative | delta (per exporter) |
| histograms | explicit buckets | explicit + exponential |
| attributes | resource | resource + scope + datapoint |

A schema/convention-version identifier accompanies every transform so
ingested series remain interpretable.

## Forge compatibility

| Sibling forge | Consumed contract | Sentinel provides | Version floor / note |
| --- | --- | --- | --- |
| `forge-doctor-api` | findings, unknowns, diagnostics over bounded evidence handoffs (`ApiHandoffBundle`, adapter-translated) | `api-sentinel/InvestigationHandoff/v1` envelope; runtime evidence | `>=0.2.0,<0.3.0` (optional dep); **requires post-PR-13 main (`035b635`)** for the `ApiHandoffBundle` `DeltaContext` fix, per the the-forge knowledge package |
| `code-atlas-forge` | structural entities/relations per repo+snapshot+identity | resolved service/operation identities | contract pinned at Cycle 5 integration |
| `the-forge` | routing, execution, receipt (`ForgeRequest`/`ForgeHandoff`, adapter-translated) | capabilities `api.runtime-observation`, `api.slo-assessment`, `api.anomaly-detection`, `api.incident-investigation`, `api.deployment-correlation` (ids verified against the live catalog) | adapter `the-forge/adapters/apisentinel` lands Cycle 5 |
| `forge-agent-bridge` | governed agent sessions + execution events | authorized context packs | Cycle 8 |
| `forge-lens` | agentic execution evidence by trace/session id | reference ids + provenance (never full trace copies) | optional integration (ADR-016) |
| `data-sentinel-forge` | pipeline/workload diagnosis | data-problem references | optional, domain-triggered (LAB-012) |
| `forge-portal` | query/operation APIs | local `/v1/*` surface for the API Operations area | Cycle 6 |
| `api-forge` | plans/implementations for suggested changes | problem + evidence + suggested change | handoff, Cycle 8 |

Every integration is optional; absence produces a named state
(`UNAVAILABLE`/`UNVERIFIED`), never simulated execution.
