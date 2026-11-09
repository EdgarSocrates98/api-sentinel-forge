# Cycle 6 — Portal API Operations

Exposes the Sentinel's deterministic, evidence-first runtime intelligence over
a minimal localhost-bound HTTP surface (`docs/04` route table, ADR-011). The
`api` context is a thin adapter: routers translate HTTP ↔ typed models and
every answer comes from the existing application use cases and persisted
repositories — no business logic in handlers, no demo data, no generic
command executor.

## Verification tails

```text
$ python -m pytest -q
277 passed in 12.38s

$ python -m ruff check .
All checks passed!

$ python -m mypy src
Success: no issues found in 84 source files
```

### Live smoke (`api-sentinel serve --host 127.0.0.1 --port 8080 --workspace <tmp>`)

```text
$ GET /v1/health
{"state":"AVAILABLE","database":"present","workspace_initialized":true,
 "capabilities":{"available":11,"declared":1}}

$ GET /v1/services            # real persisted inventory — never demo data
[checkout-api] health.state=UNVERIFIED condition=source_stale
  unknowns=[source_stale:src_…] latest_indicators=null

$ GET /v1/integrations/status
[(code-atlas-forge, unavailable), (forge-doctor-api, unverified),
 (the-forge, available)]

$ POST /v1/ingestion/jobs {"source_id":"src_…","file_ref":"batch.jsonl",
                           "idempotency_key":"ik-smoke-1"}
→ 201 job.status=COMPLETED accepted=1 batch_id=batch_…
  replay with same key → 200 {"replayed":true, job.job_id identical}

$ POST /v1/incidents/inc_…/investigations {"idempotency_key":"ik-inv-smoke"}
→ 201 inv.status=COMPLETED
  context_pack.structural_context.state=UNAVAILABLE   # no Atlas configured —
                                                      # named state, never a
                                                      # fabricated empty graph

$ GET /v1/services/ghost
→ 404 application/problem+json
  {"type":"urn:api-sentinel:sentinel.not_found","code":"sentinel.not_found",…}

$ POST /execute   /   GET /execute        → 404 problem+json (no such route)

$ api-sentinel serve --host 0.0.0.0
→ error[sentinel.validation]: refusing non-loopback bind: '0.0.0.0' — pass
  --allow-remote to opt into remote exposure (docs/07)

$ api-sentinel serve --host 0.0.0.0 --allow-remote
→ warning: binding 0.0.0.0 beyond loopback — the /v1 surface is single-user
  local with a placeholder role model (Cycle 9 authz); …then serves.
```

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Local HTTP surface implements the minimum endpoint set (17 routes) | ✅ | `api/app.py` mounts 8 routers covering every docs/04 route; `TestRouteTable::test_every_documented_route_exists` |
| 2 | No generic `/execute`-style endpoint | ✅ | no executor route exists; `/execute`, `/v1/execute`, `/v1/services/execute` → 404 problem+json; `test_no_generic_execute` |
| 3 | Import/investigation initiation: scoped authorization, concurrency limits, idempotency, audit records, async-shaped responses | ✅ | `require_scope` dep denies before lookup; `acquire_job_slot` bounded semaphore → `sentinel.concurrency_limit` 503; unique `idempotency_key` columns + replay (`201` new / `200` `replayed`); `api.scope.denied`, `ingestion.job.*`, `investigation.*` audit rows; 202-shaped `QUEUED→…` lifecycle persisted in `ingestion_jobs`/`investigations` (bounded-synchronous execution: `COMPLETED` on return, status queryable) |
| 4 | Service list with coverage from real inventory — not demo data | 🟡 API ✅ / Portal pending | `ListServiceSummaries` composes `api_services` + source/observation coverage + health + open-incident/SLO counts; Portal rendering belongs to the Portal repo |
| 5 | Per-operation indicators from real ingested series | 🟡 API ✅ / Portal pending | `ListServiceOperations` + `ShowServiceMetrics` over `runtime_observations`; declared vs observed provenance kept distinct (`provenance: declared|observed`) |
| 6 | Time-series carries explicit units and period, marks sampling and data gaps | 🟡 API ✅ / Portal pending | `MetricsView.units` (`ms`, `requests/second`, `ratio`), per-window `duration_seconds`, `qualities` histogram, `gaps[]` (missing slots reported), `excluded_by_reason`, `unknowns` |
| 7 | SLO screens show objectives, budgets, windowed history + producing revision | 🟡 API ✅ / Portal pending | `ServiceSloPanel` — definitions (`target`, `window_seconds`, `revision`), `assessments[]` each pinned to the `revision` that produced it, `budget` + `latest_assessment` state |
| 8 | Incident screens: active/historical list, timeline, evidence links, hypotheses + limitations | 🟡 API ✅ / Portal pending | `GET /v1/incidents?status=open\|closed`, `/timeline` (journal + dependency observations), `/detail` (evidence resolved to metadata, `evidence_unresolved` named), investigation `context_pack` carries `hypotheses` + `limitations` |
| 9 | Availability states honest — AVAILABLE/UNAVAILABLE/PARTIAL/UNVERIFIED/UNKNOWN; no fabricated health | ✅ | `assess_service_health` reused: absent source → `UNKNOWN`, stale → `UNVERIFIED` (`latest_indicators=null`, absent indicators listed); `test_stale_source_health_is_unverified_not_fiction` |
| 10 | Product Forge integration only on proven identity correspondence | ✅ | `ServiceLinks.correspondence.state` is `proven` only for verified-provenance `pf_api` external refs; `unresolved`/`absent` otherwise — no name matching |
| 11 | Capability permission model; denials without information leakage (LAB-016 groundwork) | ✅ | `ROLE_SCOPES` (viewer/analyst/operator/admin → docs/07 scopes); `X-Sentinel-Role` placeholder header; 403 raised *before* lookup (ghost id ≡ real id → same `sentinel.forbidden`); denials audit-recorded; real principal/token exchange deferred to Cycle 9 |
| 12 | Browser E2E journey (Overview → Service Detail → Investigation) | 🟡 pending Portal | all legs proven over live HTTP (service list → detail → POST investigation → context pack) without the CLI; browser/Portal side lives in the Portal repo |
| 13 | Portal down → CLI and local API keep working; Sentinel down states shown, not cached fiction | ✅ | the API + CLI share no dependency on the Portal; empty/absent DB → `PARTIAL`/`database_absent` at `/v1/health`; every operational read emits state + `unknowns` rather than defaults |

## Changed files

- **API surface (new):** `src/api_sentinel/api/{__init__,app,deps,schemas}.py`,
  `api/routers/{__init__,health,services,ingestion,anomalies,incidents,investigations,evidence,integrations}.py`
- **Persistence:** `migrations/0006_portal.sql` (`investigations`,
  `ingestion_jobs` — unique idempotency keys, status indexes);
  `infrastructure/storage/repositories.py` (`_OperationRepo`,
  `_InvestigationRepo`, `_IngestionJobRepo`, `AnomalyRepository.list_filtered`)
- **Application:** `application/jobs.py` (`Investigation`,
  `InvestigationPack`, `StructuralContextView`, `IngestionJob` + status enums);
  `application/ports.py` (3 new repository protocols + UoW wiring);
  `application/use_cases.py` (Cycle-6 section: service summaries/detail/
  operations/metrics/SLOs/links, incident detail/timeline, anomaly feed,
  evidence metadata, `StartInvestigation`, `StartIngestionJob`);
  `application/__init__.py` (exports)
- **Domain:** `domain/errors.py` (`ForbiddenError`, `ConcurrencyLimitError`),
  `domain/__init__.py` (exports)
- **Surface:** `cli/__init__.py` (`serve` — loopback-only default,
  `--allow-remote` opt-in with mandatory warning, `api` extra gate);
  `capabilities.py` (`api.portal-operations` → `available` — proven by this
  surface)
- **Tests:** `tests/integration/test_api_surface.py` (35 tests — every route,
  seeded workspace, problem+json 404, stale→UNVERIFIED, ingestion
  round-trip + idempotency + audit, investigation without Atlas,
  metadata-only evidence, role-scope denials without leakage, no `/execute`);
  `test_ingestion_pipeline.py`, `test_storage.py` (migration 6 expectations);
  `test_integrations_boundary.py` (portal-operations now `supported`;
  `aws-readonly-observability` is the remaining DECLARED cap)
- **Spec lifecycle:** `factory/specs/active/cycle-06-…` (moved from `inbox/`,
  Sentinel-side ACs ticked); `cycle-05-…` moved to `archive/`

## Mismatches & honest gaps

- **Portal-side ACs are API-complete, render-pending.** Criteria phrased as
  "Portal shows/screens" (rows 4–8, 12) are delivered on the Sentinel side —
  every number/trace the Portal needs is a real persisted query path — but the
  Portal UI and browser E2E live in the Portal repo, outside this commit. The
  spec boxes stay unticked until that side lands; nothing here pretends
  otherwise.
- **Bounded-synchronous jobs.** `StartIngestionJob`/`StartInvestigation`
  persist `QUEUED → RUNNING → COMPLETED` and return 201, but execution is
  synchronous within the request (bounded by `jobs_limiter`). The spec asks
  for "asynchronous responses *where appropriate*": the status-lifecycle and
  `GET …/jobs/{id}` query path already exist, so promoting execution to a
  thread/process pool is additive — deferred rather than faked.
- **Role header is a placeholder, not authn.** `X-Sentinel-Role` trusts the
  caller's claim — correct for the single-user localhost boundary (docs/07),
  explicitly *not* real authentication. Scope mapping and deny-before-lookup
  semantics are the Cycle-9 authz seam; denials are audit-recorded.
- **Structural context is UNAVAILABLE without an Atlas export.** The
  investigation context pack names the state (`structural_context.state=
  UNAVAILABLE` + `structural_reason` + unknowns entry) rather than shipping an
  empty graph that would read as "verified no dependencies".
- **Job concurrency is a bounded semaphore, not a silent queue.** Saturated ⇒
  `sentinel.concurrency_limit` 503 with `max_concurrent_jobs` in details.
- **`api` extra is optional.** `serve` names the missing dependency
  (`pip install api-sentinel-forge[api]`) instead of a traceback.

Commit: see `git log -1` (cycle commit, message
`Cycle 6: Portal API Operations surface`).
