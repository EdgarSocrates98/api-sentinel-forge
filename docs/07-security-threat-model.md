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
