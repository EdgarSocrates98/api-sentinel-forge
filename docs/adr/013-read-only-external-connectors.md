# ADR-013 — External Connectors Are Read-Only

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

External sources (CloudWatch, API Gateway, Prometheus, etc.) require
cloud credentials. The threat model treats dangerous commands and
credential leakage as primary threats.

## Decision Drivers

- Least privilege: no `AdministratorAccess`, no permanent access keys —
  prefer profiles, SSO/Identity Center, temporary/workload creds.
- An external action requires authorized scope (invariant); mutation
  belongs to governed execution via The Forge, not observation
  connectors.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Read-only connectors + least-privilege IAM, explicit account/region scope, budgets | observe only; verify identity before collect | contains blast radius; matches threat model |
| B — Connectors with implicit cloud mutation | e.g. fix config during investigation | unauthorized-mutation risk; breaks governance |

## Decision

Every external connector is read-only with minimum permissions for the
configured resources. Mutations are out of scope for connectors;
remediation flows through approved, governed execution elsewhere
(Cycle 8+).

## Consequences

**Positive**

- Credentials stay in the environment, never in Sentinel storage.
- Audit logs contain no credentials; budgets cap query cost (§22).

**Negative**

- "Fix it for me" requires a separate approved path.
- Read-only IAM setup is user work the installer can only guide.

## Compatibility

AWS notes carried into adapter design: API Gateway metric detail and
dimensions differ between REST and HTTP APIs (per-route metrics cost
extra); CloudWatch `GetMetricData` requires `NextToken`, quota and
partial-result handling — never assume one response covers the window.

## Reversal Strategy

Granting mutation later is a new capability + authorization surface,
not a connector change: it would land behind governed execution
contracts and its own ADR — connectors stay read-only regardless.

## Links

- Blueprint `prompt_inicial.md` §16.1, §16.3, §23 (ADR-013)
- `docs/07-security-threat-model.md`, `docs/14-performance-and-cost-model.md`
