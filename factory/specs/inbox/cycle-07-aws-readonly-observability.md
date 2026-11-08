---
id: cycle-07-aws-readonly-observability
title: "Cycle 7 — AWS Read-Only Observability"
agent: claude
risk: high
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Adds the first remote connector: read-only AWS ingestion from CloudWatch
Metrics and API Gateway, wrapped in query budgets and least-privilege
credential handling. Governed by `docs/07-security-threat-model.md`
(core → cloud trust boundary, §16.3 least-privilege rules),
`docs/05-telemetry-contracts.md` (external sources), blueprint §16.3, §22
(cost model and budget knobs), ADR-013 (external connectors read-only) and
ADR-014 (metadata-first collection).

_Depends: cycle-02-telemetry-ingestion (pipeline, checkpoints, quality
states)._

# Acceptance Criteria

- [ ] AWS profile/role discovery uses existing profiles and temporary
  credentials; IAM Identity Center/SSO or workload credentials preferred;
  identity is verified before any collection; account and region scope is
  explicit.
- [ ] CloudWatch Metrics adapter supports `GetMetricData` with multiple
  queries, `NextToken` pagination, quotas and partial results — a response
  is never assumed to contain the whole requested window.
- [ ] API Gateway discovery covers REST and HTTP APIs; `Count`, `4xx`,
  `5xx`, `Latency` and `IntegrationLatency` are mapped to observations;
  per-route detailed metrics are flagged as opt-in and cost-bearing.
- [ ] Collection checkpoints per source/account/region allow resuming
  without re-fetching or duplicating points.
- [ ] Query budgets are configurable on every knob from the cost model:
  metrics per query, datapoints, retrospective window, collection
  frequency, max calls per cycle, max bytes processed, concurrency, max
  runtime, retries, stored volume and estimated financial budget.
- [ ] Throttling responses are handled with bounded backoff; LAB-013 proves
  backoff, pagination and budget enforcement under simulated throttling.
- [ ] Retries carry jitter and are capped by the configured budget.
- [ ] Pagination is handled completely on every list/metrics API used.
- [ ] Documented least-privilege IAM permission set; no `AdministratorAccess`,
  no permanent access keys required; adapter code paths contain zero
  resource-mutating AWS calls (verified by test/scan).
- [ ] Collected data passes redaction before persistence; audit logs never
  contain credentials.
- [ ] Cost indicators: each collection run reports measurable API usage and
  an estimated cost figure.
- [ ] Sandbox lab runs against simulated AWS APIs covering throttling,
  pagination and partial results; an authorized real test-account
  homologation is performed and recorded separately from the simulated
  suite.
- [ ] Read-only verification: a test proves no mutating AWS API call is ever
  issued on any code path (no resource mutation, verifiably).

# Constraints

- External connectors are opt-in; external input stays disabled by default.
- Read-only only (ADR-013): no cloud mutation, ever — this is verifiable,
  not aspirational.
- Credentials are held by the environment, never by the Sentinel; audit
  logs are credential-free.
- An external action requires an authorized scope (invariant).
- Every egress is governed by the budget knobs — collection halts when a
  budget is exhausted rather than degrading silently.
- Bounded contexts: the `detection`/`metrics` domains never know AWS exists;
  the connector is an `integrations` adapter emitting standard
  `RuntimeObservation`s.
- A missing collector ≠ an unavailable service; AWS collection gaps surface
  as `PARTIAL`/`STALE` quality, never fabricated health.
- Metadata collected by default; no indiscriminate production-log scraping
  (ADR-014); collection expands only during an authorized investigation.

# Review Notes

- Scan the adapter for write verbs — the read-only proof must be mechanical,
  not a code-read promise.
- Check partial-result honesty: truncated/paginated windows record quality
  loss.
- Confirm budget exhaustion produces a named terminal state with remaining
  work checkpointed.
- Verify the real-account homologation record is evidence (run output under
  `factory/runs/`), not a claim.
- Confirm credential discovery never logs or persists secret material.

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: CloudWatch Logs, X-Ray, Azure Monitor and Datadog adapters,
  any write/remediation action, multi-account organization discovery,
  continuous always-on collection beyond configured budgets.
- Riskiest assumption: CloudWatch + API Gateway metric granularity suffices
  to feed Cycle-3 indicators without log-level telemetry.
- Smallest acceptable version: profile discovery + CloudWatch Metrics with
  pagination, budgets and checkpoints, plus the sandbox lab with LAB-013
  green and a recorded test-account homologation.
