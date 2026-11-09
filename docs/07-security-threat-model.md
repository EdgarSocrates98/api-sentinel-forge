# 07 — Security Threat Model

Scope: the local Sentinel core, workspace store, ingestion surfaces, CLI,
local HTTP API (Cycle 6), opt-in external connectors and Forge handoffs.
Internet exposure: none by default (localhost bind). Deployment: single
user workstation / lab box.

## Assets

Telemetry payloads (may embed request metadata), evidence artifacts,
incident/investigation records, source configurations, workspace DB,
audit journal, cloud credentials (read-only, held by environment — never
by Sentinel), Forge handoff envelopes.

## Trust boundaries

1. External files/network → ingestion validators.
2. Ingestion → persistence (schema/quality gate + quarantine).
3. Core → workspace store (paths, symlinks, perms).
4. Core → Forge adapters (handoff envelopes, authorization).
5. Agent context ← untrusted logs/traces/docs (prompt-injection surface).
6. Local HTTP API ← Portal/clients (authz, scope).
7. Core → cloud APIs (read-only connectors, egress budgets).

## Attacker capabilities

Can supply crafted telemetry/log files, malformed OTLP exports, hostile
strings in payloads, and paths under configured roots. Cannot (assumed):
read arbitrary local files outside authorized roots, call cloud APIs with
Sentinel's credentials, or inject into agent instructions through data
channels — these are mitigations to verify.

## Threats (abuse paths)

| Threat | Risk | Mitigation |
| --- | --- | --- |
| SSRF via configured sources | internal/metadata endpoints reached | allowlist, destination validation, safe DNS resolution |
| Path traversal | arbitrary file read/write | authorized roots, canonicalization, symlink checks |
| Prompt injection | malicious instructions in logs/traces | data never becomes instruction; authorized tools only; instruction isolation |
| Secrets in logs | credential leakage | minimization, redaction, no payload capture by default |
| Cardinality explosion | memory/storage exhaustion | route normalization, budgets |
| Fabricated telemetry | manufactured incidents | provenance, source authentication, validation |
| Replay | duplicated observations | idempotency keys, checkpoints |
| Cross-workspace access | information exposure | principal+workspace authorization |
| Supply chain | compromised dependency | lockfiles, audit, controlled publishing |
| Dangerous commands | accidental mutation | execution mediated by The Forge + approval |

## Permission model (capability-based)

| Role | Query | Import | Investigate | Manage sources | Approve changes |
| --- | --- | --- | --- | --- | --- |
| Viewer | yes | no | no | no | no |
| Analyst | yes | if authorized | yes | no | no |
| Operator | yes | yes | yes | scoped | per policy |
| Admin | yes | yes | yes | yes | not automatic |

A Sentinel admin never auto-gets external-infrastructure change rights —
those belong to execution governance and the target system.

## Cloud connectors (least privilege)

- Read-only permissions, minimum scope, explicit account/region, separate
  environments, identity verified before collect, no `AdministratorAccess`,
  no permanent access keys (prefer profiles/SSO/temporary creds).
- API Gateway metric detail/dimensions differ between REST and HTTP APIs;
  per-route metrics cost extra.
- CloudWatch `GetMetricData`: handle `NextToken`, quotas, partial results;
  never assume one response covers the requested window.

## Sensitive-data policy

Metadata-first collection; headers/bodies off by default; sensitive ids
transformed; URL query values not persisted; redaction before write and
again at presentation; explicit purge policy; evidence-access audit;
credential rotation/revocation; workspace isolation. LGPD: mechanisms for
minimization, purpose, security and configurable retention — legal
conformity depends on the installing organization.

## Open assumptions

- Single-user local operator; multi-user authz is Cycle 9.
- Local HTTP binds to localhost by default.
- Agent data channels are firewalled from instructions by adapter design.

## Cycle-9 delta — newly exposed surfaces, re-evaluated

Cycle 9 hardened the release candidate without adding product surface,
but Cycles 6–8 landed surfaces this threat model's "single-user local
operator" assumption did not fully cover. The delta below is appended,
not rewritten into history.

### New entry points

| Surface | Entry point | Mitigations in place | Residual risk |
| --- | --- | --- | --- |
| `api-sentinel serve` (FastAPI) | local HTTP on loopback | binds `127.0.0.1` by default; `--allow-remote` is explicit and audited; **Cycle 10**: every `/v1/*` route except `/v1/health` requires a credential — `Authorization: Bearer` (workspace token, `secrets.token_urlsafe(32)`, `hmac.compare_digest`, optional TTL rotation) or a signed `X-Sentinel-Principal` (HMAC-SHA256 over principal+role+workspace+timestamp+nonce, ±300 s skew, single-use nonce). Self-declared `X-Sentinel-Role` is rejected `401`, never trusted (C10-F007) | **low–medium**: real credentials gate the surface; residual = `--allow-remote` still places a shared-secret surface on the network, and the workspace token file's `0600` is best-effort under Windows ACLs (documented). Integrated mode requires deployers to protect the shared secret like any credential. |
| AWS connector (Cycle 7) | cloud egress + credentials | credentials held by environment only, never by the Sentinel; read-only scope documented; egress declared in tests via the socket guard; simulated path egress-free | **low–medium**: `--allow-remote`-style misuse is impossible (connector egress is scoped to AWS endpoints), but real-account homologation is still pending — least-privilege IAM guidance is written, not yet exercised against a live account. |
| `agent_sessions/` handoff dir | Bridge session envelopes | workspace-local files; peer never writes into the Sentinel; requests are approval-gated before any action | **low**: an envelope is data, not instruction — the instruction/data firewall assumption now has a concrete carrier; adapters must keep it that way. |
| `backup` / `restore` / `retention` CLI | file-system writes + deletes | restore refuses live overwrite without `--force` and moves replaced files aside; retention is dry-run by default; evidence protection (ADR-015) proven by test; receipts + `operations.*` audit rows journal every applied operation | **low**: `--force` and `--apply` are operator foot-guns by design (explicit flags), audited but not interactive-confirmed. |

### Verified this cycle (was assumption, now evidence)

- Denials precede resource lookup — no existence leakage — and emit
  `api.scope.denied` audit rows (`tests/integration/test_authz_matrix.py`,
  LAB-016).
- The full test suite runs with non-loopback sockets hard-blocked
  (`tests/conftest.py`) — the offline claim is now mechanical, not
  documented intent.
- Retention deletion paths were re-audited: evidence referenced by live
  incidents/anomalies/investigations is never a deletion candidate
  (`tests/integration/test_retention.py`) — zero silent evidence loss.

### Residual risks accepted for the RC

1. ~~`X-Sentinel-Role` placeholder auth~~ — **resolved Cycle 10 Wave 1**
   (C10-F007): bearer token + HMAC-signed principal; see the Cycle-10
   delta below and `docs/cycles/cycle-10/05-security-hardening.md`.
2. Real AWS-account homologation not executed (no credentials in dev) —
   checklist in `docs/cycles/cycle-07-report.md`.
3. Live Bridge/Doctor/Lens peer round-trips unverified — handoff-file
   transport emits envelopes; peer consumption is out of local scope.
   Cycle 10 hardened the emission/ingest boundary (correlation ids,
   canonical-path + symlink guards, bounded reads, transport≠execution
   receipts) — the peer side remains unproven.
4. Binary-version downgrade is intentionally unsupported — restore
   from backup is the rollback path (docs/15).

## Cycle-10 delta — real authentication replaces the placeholder

Cycle 10 Wave 1 retired the self-declared-role model (C10-F007) and
hardened the file/handoff boundaries. Auth mechanics live in
`infrastructure/security/` — domain code stays credential-free.

### API authentication model (as built)

- **Local mode** — `Authorization: Bearer <token>`. Resolution order:
  `API_SENTINEL_TOKEN` env → `--token-stdin` → `<workspace>/token`
  (generated via `secrets.token_urlsafe(32)` on first serve/install,
  written `0600` best-effort — Windows maps `os.chmod` to the read-only
  attribute only, so NTFS-ACL equivalence is *not* claimed). Comparison
  is `hmac.compare_digest`. `api.token_ttl_seconds` config enables
  file-token rotation; env/stdin tokens are TTL-exempt (their lifetime
  is the deployer's). The token grants the configured `token_role`
  (default `operator`).
- **Integrated mode** — `X-Sentinel-Principal` honored *only* with a
  valid `X-Sentinel-Signature`: hex HMAC-SHA256 over
  `principal\nrole\nworkspace\ntimestamp\nnonce` keyed by
  `API_SENTINEL_SHARED_SECRET` / `api.shared_secret` (fallback: the
  workspace token). Timestamp ±300 s, nonce single-use (bounded by the
  skew window), workspace id must match. A missing/forged/stale/
  replayed signature is a typed `401` — the role in the signed claim is
  then mapped through `ROLE_SCOPES`; unknown roles are refused.
- **In-process mode** — `ApiContext(auth=None)` keeps the Cycle-6
  placeholder *for direct injection only* (TestClient/tests). `serve`
  and `create_app()` without an injected context always resolve a real
  credential — the placeholder is unreachable on a served path.
- Every `401` carries `WWW-Authenticate: Bearer realm="api-sentinel"`
  and a `application/problem+json` body with a machine-readable
  `details.reason`. Authn failures and scope denials are audit-journaled
  best-effort (`api.auth.denied` / `api.scope.denied`); credential
  material never lands in audit rows. Scope denial precedes any
  resource lookup — a `403` never reveals existence.
- CORS is deny-by-default: `api.cors_origins` opt-in only,
  `allow_credentials=False` — a wildcard origin is never credentialed.
  Forwarded/proxy headers are never consulted for identity.

### Handoff / file-boundary hardening

- `correlation_id` on every `InvestigationHandoff/v1` and receipt —
  request, receipt, and response are correlatable.
- Receipts separate `transport_state` (`emitted`/`received`) from
  `execution_state` (`pending`/`completed`/`failed`) — an emitted file
  is never reported as executed remote work.
- All handoff/session/receipt paths canonicalize under the workspace
  root (`safe_child_name`, `safe_slot`) — `..` segments and symlink
  escapes are refused before any write/read.
- Peer-supplied files are read through `read_text_bounded`
  (default 32 MiB) — hostile bundles can't exhaust memory; doctor
  bundles are correlation-checked against the handoff id; Atlas exports
  reject duplicate service/edge ids.
- Evidence hashes enforce `sha256:<64 lower-hex>` in both the Pydantic
  model and the JSON schema; provider-response correlation must match
  the request.

### Verified this wave

- Live smoke: unauthenticated `/v1/services` → `401` +
  `WWW-Authenticate`; `X-Sentinel-Role: admin` alone → `401`; valid
  bearer → `200`; `/v1/health` stays ungated.
- Negative battery: `tests/security/test_api_auth.py` (spoof, replay,
  stale timestamp, wrong workspace, bad scheme, expiry, precedence);
  `test_handoff_security.py`; `test_storage_recovery.py`; extended
  `tests/contract/test_schema_compat.py` — all green at 513 tests.
