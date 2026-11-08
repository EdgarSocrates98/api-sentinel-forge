# ADR-003 — Local-First and Offline-First

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

The product ingests telemetry, contracts and (later) cloud connector
data. The commercially and technically useful first milestone is a local
MVP, not a distributed cloud platform; external connectors must be
opt-in.

## Decision Drivers

- User trust: telemetry and evidence stay on the operator's machine.
- Lab-first development: E2E scenarios must run offline.
- Cycle 0.5.1 readiness: local deterministic foundation approved;
  external AI/cloud sessions remain outside homologation.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Local-first + opt-in connectors | everything works offline; cloud read-only connectors are explicit opt-ins | matches gates and threat model |
| B — SaaS/cloud mandatory | hosted control plane | breaks offline labs, raises data-policy burden, out of MVP scope |

## Decision

Local-first and offline-first. External input is disabled by default;
cloud connectors are read-only opt-ins (ADR-013) with budgets (§22).

## Consequences

**Positive**

- Tests run with network blocked (engineering target).
- Credentials never need to reach the core; data minimization by design.

**Negative**

- No managed multi-tenant story; enterprise profile is a future stage.
- Continuous collection requires the operator to opt in and configure.

## Compatibility

All public contracts are local-file/subprocess friendly. Portal
integration consumes a localhost API; nothing requires egress.

## Reversal Strategy

Adding a hosted mode later means implementing the same versioned
contracts behind a remote transport — contracts and storage layout do
not assume local files semantically (paths are workspace-relative).
Reversal cost is in ops, not in the domain model.

## Links

- Blueprint `prompt_inicial.md` §1 (execution model), §19, §23 (ADR-003)
- `docs/07-security-threat-model.md`
