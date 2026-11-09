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
| `api-sentinel serve` (FastAPI) | local HTTP on loopback | binds `127.0.0.1` by default; `--allow-remote` is explicit and audited; every request requires a declared `X-Sentinel-Role` | **medium**: the role header is a documented placeholder — bearer-less, no signature. Acceptable on loopback with a local trusted operator; NOT sufficient if `--allow-remote` is used on a hostile network. Recorded: real authn/z is deferred to a post-RC cycle with an explicit design decision. |
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

1. `X-Sentinel-Role` placeholder auth — loopback-only trust model;
   `--allow-remote` shifts the burden to the deployer. Tracked for a
   dedicated authz cycle.
2. Real AWS-account homologation not executed (no credentials in dev) —
   checklist in `docs/cycles/cycle-07-report.md`.
3. Live Bridge/Doctor/Lens peer round-trips unverified — handoff-file
   transport emits envelopes; peer consumption is out of local scope.
4. Binary-version downgrade is intentionally unsupported — restore
   from backup is the rollback path (docs/15).
