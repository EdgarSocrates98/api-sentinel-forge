# Cycle 10 — Cross-repo contract inventory (Wave 0)

Real names/versions quoted from sibling source at the SHAs in
`evidence/baseline.json`. Verdict per seam: **COMPATIBLE / INCOMPATIBLE /
ORPHAN / UNVERIFIED**. Detail also in `evidence/contract-conformance.json`.

## 1. The Forge provider protocol (`forge/v1`) — COMPATIBLE at dict level

| Side | Symbol | File:line |
|---|---|---|
| the-forge | `Request {protocol="forge/v1", kind="Request", op, request_id, payload}` | `src/theforge/contracts/envelope.py:40-46` |
| the-forge | `Response {protocol, kind="Response", request_id, op, producer, status, payload, error?, limitations, unknowns}` | `envelope.py:49-58` |
| the-forge | `ExecutionResult` (frozen dataclass; `RESULT_SCHEMA="theforge/ExecutionResult/v1"`) | `contracts/result.py:38,139-165` |
| sentinel | `SentinelBoundary.handle` emits the same Response dict shape; `FORGE_PROTOCOL="forge/v1"` | `integrations/the_forge/boundary.py:200-271`, `handoff.py:23-24` |
| sentinel adapter | stdin Request → `SentinelBoundary.handle()` → stdout Response | `adapters/apisentinel/src/theforge_apisentinel/__main__.py:33-51` |

Gap: field-compatible, but `_EXECUTORS` covers 2 of 7 declared pairs → C10-F010.

## 2. Doctor handoff (`ForgeRequest` v2 → `ApiHandoffBundle`) — TRANSPORT-BROKEN

| Side | Symbol | File:line |
|---|---|---|
| doctor | `PROTOCOL_VERSION = 2`; `ForgeRequest {protocol_version, request_id, target*, capabilities, context_refs, delta, clock}` | `handoff/protocol.py:25,124-167` |
| doctor | `DoctorBoundary(context: ProjectContext, clock?) .handle() -> ApiHandoffBundle` | `handoff/boundary.py:50-116` |
| doctor | `ApiHandoffBundle {handoff_version 1|2, handoff_id, analysis_rev, ...}` + `to_dict()` (probed OK) | `handoff/model.py:107-195` |
| doctor | `ProjectContext.from_root` — **lives in `core.context`, NOT `handoff.context`** | `core/context.py:29-35` (probe: ImportError from `handoff.context`) |
| sentinel | `ForgeRequestV2` — field-for-field dict twin, `extra="forbid"`, `exclude_none` | `integrations/doctor/translate.py:32-46` |
| sentinel | stale import `from forge_doctor_api.handoff.context import ProjectContext` | `integrations/doctor/adapter.py:219-221` → C10-F009 |
| consumer-side file ingest | **does not exist** — doctor CLI offers MCP stdio (`handoff/mcp_server.py`) and in-process only | forge-doctor-api `cli/__init__.py:1387` |

Emission dict: COMPATIBLE. Execution seam: BROKEN (stale import) + no file consumer → C10-F003/F009.

## 3. PortalApi (`forge/PortalApi/1`) vs sentinel `/v1` — INCOMPATIBLE

| Side | Symbol | File:line |
|---|---|---|
| product-forge | `SCHEMA="forge/PortalApi/1"`, `API_PREFIX="/api/v1"`, envelope `{schema, correlation_id, data|error}`, `PORTAL-*` codes | `src/product_forge/_forge_portal.py:38-39,627-810` |
| product-forge | session: `X-Forge-Session` header / `forge_session` cookie; `PORTAL-SESSION` on miss | `_forge_portal.py:40-41,674` |
| sentinel | `application/problem+json` `ProblemDetails{type,title,status,detail,code,details}` | `api/app.py:64-79`, `api/schemas.py` |
| sentinel | auth: `X-Sentinel-Role` raw header → ROLE_SCOPES | `api/deps.py:117-144` |

Different envelope, different auth, different error taxonomy → C10-F005.
A contract adapter (not config) is required for portal↔sentinel.

## 4. Atlas export (`api-sentinel/AtlasExport/v1`) — ORPHAN

| Side | Symbol | File:line |
|---|---|---|
| sentinel | `AtlasExportV1.expected_schema="api-sentinel/AtlasExport/v1"` | `contracts/v1/models.py:168` |
| sentinel | `FileStructuralContext.load(path)` — pydantic-validated | `integrations/atlas/file_adapter.py:45-75` |
| code-atlas | **no `AtlasExport`, no `api-sentinel` reference anywhere in src/** | grep → 0 hits (C10-F011) |
| code-atlas | emitted contracts: `forge/CodeAtlasInstallReceipt/v1`, `forge/SpecialistAgenticManifest/v1`, `theforge/KnowledgeContextPack/v1`, `theforge/ObsidianNoteRef/v1` | code-atlas src/ |

## 5. Bridge session (`AgentSessionRequest`) — UNVERIFIED

| Side | Symbol | File:line |
|---|---|---|
| sentinel | `AgentSessionRequest` → `agent_sessions/<id>/session_request.json` (redacted) | `investigation/agent_session.py`, `bridge/adapter.py:52-82` |
| bridge | `forge/AgentBridgeContract/v1`, `forge/ProductAgentSessionContext/v2`, `forge/SessionIntegrity/v1` | forge-agent-bridge src/ (0.2.1) |
| bridge | file consumer for `session_request.json` | **not found in this audit** |

## 6. Sentinel-owned v1 contracts (emitted)

`api-sentinel/{ServiceIdentity,RuntimeObservation,Anomaly,
InvestigationHandoff,AtlasExport,TelemetrySource,DeployManifest,
EvidenceArtifact}/v1` — `contracts/v1/models.py:37-224`; plus
`BackupManifest/v1` (e2e S12 output).

## Summary matrix

| Seam | Wire contract | Verdict | Blocking finding |
|---|---|---|---|
| the-forge seam | forge/v1 Request/Response + ExecutionResult/v1 | COMPATIBLE (executors 2/7) | F010 |
| doctor handoff | ForgeRequest v2 → ApiHandoffBundle v1/v2 | emission COMPATIBLE; execution BROKEN | F003+F009 |
| bridge session | AgentSessionRequest file | UNVERIFIED | F004 |
| portal↔sentinel | PortalApi/1 vs problem+json | INCOMPATIBLE | F005 |
| atlas export | api-sentinel/AtlasExport/v1 | ORPHAN | F011 |
| mcp serve | manifest mcp_command | ABSENT | F012 |

---

## Wave-2 reconciliation (current state — supersedes verdicts above where noted)

| Seam | Wave-2 verdict | Evidence |
|---|---|---|
| the-forge seam | **INTEGRATED — 8/8 declared pairs execute through the real runtime** (registry refresh state=ready; routed `ask` runs with receipts) | `evidence/theforge/runs/*/receipt.json` · `08-the-forge-routing.md` — F010/F015/F018 FIXED |
| doctor handoff | **INTEGRATED — real in-process execution** (0.3.1 `core.context.ProjectContext`; 10 findings / 9 unknowns ingested) | `evidence/doctor_inprocess_run.json` · `06-doctor-integration.md` — F009 FIXED |
| bridge session | **NAMED — `unverified | file`** (no session-import seam exists in forge-agent-bridge; delegated envelopes only) | `integrations` status · `09-agent-bridge-and-lens.md` |
| portal↔sentinel | INCOMPATIBLE — unchanged, contract adapter required | F005 (Wave 3) |
| atlas export | **INTEGRATED via real index store** (`integrations.atlas.db_path` → `DbStructuralContext`, read-only, READY-snapshot-gated; `AtlasExport/v1` file seam remains supported) | `evidence/atlas/` · `evidence/atlas_correlate_run.json` · `07-code-atlas-integration.md` — F011 FIXED |
| mcp serve | IMPLEMENTED (Wave 1) | `src/api_sentinel/mcp_server.py` — F012 FIXED |
| lens refs | **NAMED — `unavailable | file`** (no `forge_lens` import / `lens.refs` manifest in lab) | `09-agent-bridge-and-lens.md` |
| apiforge handoff | **NAMED — `unverified | handoff_file`** | `09-agent-bridge-and-lens.md` |
| data-sentinel handoff | **NAMED — `unverified | handoff_file`** (stale-data only; never generic telemetry) | `09-agent-bridge-and-lens.md` |
| InvestigationHandoff requiredness | **ALIGNED — `payload`/`constraints` required in model = schema source of truth** | `contracts/v1/models.py` — F014 FIXED |
| ForgeManifest default_action | **FIXED — manifest declares only executor-backed pairs** (was `default_action: null` → invalid) | `boundary.py` describe — F015 FIXED |
| ExecutionResult evidence | **FIXED — sentinel refs re-emitted as declared Evidence** (was FORGE-RESULT-DANGLING-EVIDENCE) | `boundary.py` — F018 FIXED |
| conformance execute probe | **NAMED — `APISENT-WORKSPACE-UNAVAILABLE` in scratch workspaces** (harness probes an empty dir; honest refusal by design) | `evidence/theforge/conformance_check.json` — F016 recorded |
| forge/v1 param channel | **WORKED AROUND — explicit param → single-service → named ambiguity** (wire carries no task params) | `boundary.py` scoping — F017 recorded |
