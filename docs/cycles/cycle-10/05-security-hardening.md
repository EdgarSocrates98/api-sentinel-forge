---
cycle: 10
deliverable: security hardening — API auth, handoff/file boundary, evidence
wave: 1
---

# Cycle 10 Wave 1 — Security hardening

Owns **C10-F007** (API trusted self-declared `X-Sentinel-Role`) and the
file/handoff boundary hardening called out by `prompt_evo_cycle10.md`
§5.2. Evidence: `evidence/security-results.json`. Threat-model delta:
`docs/07-security-threat-model.md` §Cycle-10.

## 1. C10-F007 — self-declared role → real credentials

**Before (verified at wave 0):** `deps.py` read the effective role
verbatim from the client header; any HTTP client could send
`X-Sentinel-Role: admin` and receive the `approve_changes` scope.
Ungated reads returned `200` to arbitrary role strings.

**After:** every `/v1/*` route except `/v1/health` requires a verified
credential (`api/deps.py::_authenticate`). Two modes, both real:

| Mode | Credential | Verification |
| --- | --- | --- |
| local | `Authorization: Bearer <token>` | `secrets.token_urlsafe(32)` workspace token; `hmac.compare_digest`; optional `api.token_ttl_seconds` rotation |
| integrated | `X-Sentinel-Principal` + `X-Sentinel-Signature` | HMAC-SHA256 over `principal\nrole\nworkspace\ntimestamp\nnonce`; ±300 s skew; single-use nonce; workspace-id match |

Token resolution order: `API_SENTINEL_TOKEN` → `--token-stdin` →
`<workspace>/token` (generated + printed once; `0600` best-effort —
Windows ACL caveat documented, never overclaimed). `install` generates
the token so it exists before first serve.

A bare `X-Sentinel-Role` with no credential is `401
missing_credentials` — the header is now only meaningful *inside* a
signed integrated-mode claim. Role→scope mapping (`ROLE_SCOPES`:
viewer/analyst/operator/admin per docs/07) is enforced server-side;
scope denial precedes any lookup (no existence oracle) and denials are
audit-journaled best-effort (`api.auth.denied`, `api.scope.denied`) —
credential material never reaches an audit row.

**Compat seam (documented, constrained):** `ApiContext(auth=None)` is
the in-process/TestClient mode where the old placeholder applies. It is
reachable only by direct `ApiContext` injection — `serve` and
`create_app()` without an injected context always resolve a real token
(`_build_context` → `resolve_auth`; `ensure_local_token` never returns
a credential-less result). Serving is never credential-less.

HTTP contract on failure: `401` + `WWW-Authenticate: Bearer
realm="api-sentinel"` + `application/problem+json` with
`details.reason` (missing_credentials, bad_token, token_expired,
bad_signature, stale_timestamp, replay, workspace_mismatch, …). CORS is
deny-by-default; `api.cors_origins` is explicit opt-in and never
credentialed. Forwarded/proxy headers are never consulted for identity.

**Live smoke (2026-11-09, `serve --workspace` tmp dir, port 8471):**

| Request | Result |
| --- | --- |
| `GET /v1/health` no auth | `200` (ungated by design) |
| `GET /v1/services` no auth | `401` + `WWW-Authenticate`, problem+json `missing_credentials` |
| `GET /v1/services` `X-Sentinel-Role: admin` | `401` (the F007 spoof is dead) |
| `GET /v1/services` `Authorization: Bearer <token>` | `200` |
| `GET /v1/capabilities` bearer | `200`, honest `stage`+`status` |

## 2. Handoff / file-boundary hardening

- **Correlation:** `InvestigationHandoffV1.correlation_id` (model +
  schema) propagated onto every receipt — request/receipt/response are
  correlatable end-to-end.
- **Transport ≠ execution:** receipts carry `transport_state`
  (`emitted`/`received`) separate from `execution_state`
  (`pending`/`completed`/`failed`). An emitted handoff file is never
  reported as completed remote work.
- **Path safety:** `infrastructure/security/paths.py`
  (`safe_child_name`, `safe_slot`, `resolve_under_root`) guards every
  handoff/session/receipt location — `..` segments, absolute escapes,
  and symlink escapes are refused before any I/O. Applied in
  doctor/apiforge/data_sentinel/bridge adapters and `receipts.py`.
- **Bounded reads:** `read_text_bounded` (default 32 MiB) wraps every
  peer-supplied file read — doctor bundles, apiforge payloads, Atlas
  exports, Lens artifacts, change files. Oversized input is a typed
  `ValidationError`, never an OOM.
- **Correlation checks on ingest:** doctor response bundles must match
  the handoff id they answer; provider responses must echo the request
  correlation; Atlas exports reject duplicate service/edge ids.
- **No subprocess paths** exist in `src/` (grep-verified); therefore no
  `shell=True`/timeout exposure — the AC element is trivially met.

## 3. Contract tightening (§5.3)

- `EvidenceArtifactV1.hash` enforces `sha256:<64 lower-hex>` in both the
  Pydantic model and `contracts/v1/evidence_artifact.schema.json`.
- `AtlasExportV1` rejects duplicate service ids and edge ids.
- Negative suite extended in `tests/contract/test_schema_compat.py`:
  unknown version, removed field, missing required, bad
  unit/dimension/timestamp, duplicate ids, mismatched correlation, bad
  hash, truncated payload, broken envelope reference — each a typed
  refusal.

## 4. Storage recovery (§5.5)

`tests/security/test_storage_recovery.py`: built wheel carries
migrations (real `uv build` + zip inspection), clean install / rerun /
upgrade-with-data / failed-migration rollback, interrupted + corrupted
backup refused before writes, restore requires `--force` and restores
with verified integrity, crash-mid-commit leaves zero partial rows
(retry re-ingests cleanly), orphan blobs reported-never-deleted,
retention protects open-incident evidence.

## 5. C10-F012 — `mcp serve` implemented (bonus scope)

`src/api_sentinel/mcp_server.py` + `api-sentinel mcp serve`: a read-only
stdio MCP server (installed `mcp` package — no new required dep;
optional-import guarded). Four tools: `sentinel_list_services`,
`sentinel_capabilities`, `sentinel_health`, `sentinel_list_incidents` —
read-only by construction; no mutation tools exist.
`forge.agentic.json`'s `mcp_command` now resolves.

## 6. Deferred findings (unchanged, with wave)

| Finding | Wave | Rationale |
| --- | --- | --- |
| C10-F009 stale ProjectContext import | 2 | Doctor-side seam; needs doctor wheel homologation |
| C10-F010 boundary executor gap (5/7 pairs refuse) | 2 | Executors map to existing use cases; honest refusal today |
| C10-F011 AtlasExport producer missing | 2 | Producer lives in code-atlas-forge, out of this repo |
| C10-F005 Portal↔Sentinel wiring | 3 | Cross-repo contract bridge |
| C10-F006 ingestion N+1 | 4 | Performance, not correctness |

## 7. Verification

- `pytest -q` → **514 passed** (auth battery, handoff, storage,
  contract negatives, MCP tools).
- `ruff check src tests lab` → clean. `mypy src` → clean (115 files).
- Live smoke table in §1 — real `uvicorn` + `curl`, token printed once.
