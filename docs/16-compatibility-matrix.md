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

Verified versions below were homologated in Cycle 10 (receipts under
`docs/cycles/cycle-10/evidence/`); "named" means the seam is a real,
honest named state, not a failure.

| Sibling forge | Consumed contract | Sentinel provides | Verified version / state |
| --- | --- | --- | --- |
| `forge-doctor-api` | findings, unknowns, diagnostics over bounded evidence handoffs (`ApiHandoffBundle` v1/v2, `ForgeRequest` protocol v2) | `api-sentinel/InvestigationHandoff/v1` envelope; runtime evidence | **0.3.1 INTEGRATED** — in-process `DoctorBoundary` run completed (10 findings / 9 unknowns, receipt `rcpt_hnd_h_01M70777F9VS1TQ2XNJ8ZD5X8Q`); `ProjectContext` resolved from `core.context` (0.3.x) with `handoff.context` fallback (<0.3) |
| `code-atlas-forge` | SQLite index store (`atlas.db`: repositories/snapshots/domain_entities/domain_relations/evidence) via `DbStructuralContext` | resolved service/operation identities (`code_atlas_identity` external ref) | **1.1.1 INTEGRATED** — real index run (snapshot 11) + correlate → 3 declared CONTAINS edges; `api-sentinel/AtlasExport/v1` file seam still supported for pre-exported manifests |
| `the-forge` | `forge/v1` Request/Response + `theforge/ExecutionResult/v1` | provider `api-sentinel-forge` via `adapters/apisentinel` (`theforge-apisentinel-adapter==0.1.0`); 8 execute pairs | **INTEGRATED** — registry `state=ready`, 7 capabilities, routed `ask` runs receipted (`evidence/theforge/runs/`); adapter commit `5aea039` on `the-forge@cycle-10-apisentinel` |
| `forge-agent-bridge` | governed agent sessions + execution events | authorized context packs (`AgentSessionRequest` envelopes, redacted, workspace-bounded) | **NAMED `unverified | file`** — no session-import seam exists bridge-side; delegated, never executed (F004) |
| `forge-lens` | agentic execution evidence by trace/session id | reference ids + provenance (never full trace copies) | **NAMED `unavailable | file`** — no `forge_lens` module / refs manifest configured (optional, ADR-016) |
| `data-sentinel-forge` | pipeline/workload diagnosis | data-problem references | **NAMED `unverified | handoff_file`** — stale-data handoffs only; never generic telemetry (optional, LAB-012) |
| `forge-portal` | `application/problem+json` `/v1/*` surface + bearer token | local `/v1/*` surface for the API Operations area | **INTEGRATED** — `cycle-10-sentinel` branch: dedicated `SentinelApi` adapter, same-origin `/sentinel` proxy pattern (CSP `connect-src 'self'`), Chromium journey 19/19 |
| `api-forge` | plans/implementations for suggested changes | problem + evidence + suggested change | **NAMED `unverified | handoff_file`** — implementation-request envelopes; no consumer proven |
| `product-forge` | — | — | deliberately independent: `/api-operations` renders fully with Product Forge absent (separation §7.3) |

Every integration is optional; absence produces a named state
(`UNAVAILABLE`/`UNVERIFIED`), never simulated execution.

## Platform / packaging (Cycle 10 re-verified)

| Axis | Verified |
| --- | --- |
| Python | 3.11 (mypy floor) / 3.12 (dev venv + CI + fresh-venv smoke) |
| OS | Windows (dev + CI `windows-latest`) · Linux (CI `ubuntu-latest`) |
| Install | `uv build` wheel+sdist clean; wheel ships `src/api_sentinel` incl. 6 SQL migrations; sdist adds `tests/` + `contracts/v1/*.schema.json`; fresh-venv wheel install → `install`/`doctor`/`capabilities`/`mcp` (extra-gated) all green; workspace path-with-spaces + repeat-install idempotent |
| Extras | `api` (fastapi+uvicorn — serve), `aws` (boto3 — collectors), `mcp` (mcp — stdio server), `dev` (test/lint/type gates) |
| Auth surface | `Authorization: Bearer` workspace token or HMAC integrated principal; loopback bind default; `--allow-remote` opt-in with loud warning |
