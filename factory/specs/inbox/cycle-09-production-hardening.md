---
id: cycle-09-production-hardening
title: "Cycle 9 — Production Hardening & Release"
agent: claude
risk: high
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
  - "python -m build"
---

# Context

Final cycle: proves the product meets operational criteria in a
representative environment — scale, recovery, security verification,
cross-platform CI, release candidates and operator documentation. Governed
by `docs/07-security-threat-model.md` (re-evaluation target), blueprint
§21 (quality pyramid and §21.2 engineering targets), §22 (cost/budget),
§24 (non-functional requirements, failure strategy), §25 (distribution and
compatibility), §26 (required documentation set), ADR-004 (SQLite first)
and ADR-015 (configurable retention).

_Depends: all previous cycles — cycle-02-telemetry-ingestion,
cycle-03-runtime-health-slo, cycle-04-detection-incident-lifecycle,
cycle-05-cross-forge-intelligence, cycle-06-portal-api-operations,
cycle-07-aws-readonly-observability,
cycle-08-agentic-investigation-change._

# Acceptance Criteria

- [ ] Scale benchmark executed on target hardware with throughput, memory
  and time measured and recorded under `factory/runs/` — numbers are
  documented, no artificial threshold is invented.
- [ ] Performance and retention: the configurable retention policy
  (ADR-015) is enforced and verified; performance stays within measured
  budgets on the benchmark corpus.
- [ ] Recovery and backup: a documented backup/restore procedure passes a
  restore scenario — LAB-014 proves state and history stay consistent
  across restart.
- [ ] Authentication/authorization verification: the capability permission
  model is exercised — LAB-016 proves an unauthorized principal is denied
  without information leakage.
- [ ] Connectors under partial failure: each integration is degraded
  independently (Doctor down, Atlas down, Portal down, Bridge down,
  telemetry source down) and the documented failure-strategy behavior is
  verified — the Sentinel is never a global point of failure.
- [ ] Self-observability: the Sentinel exposes its own operational
  indicators (ingestion lag, quarantine size, storage health, connector
  states).
- [ ] Threat model re-evaluated against the shipped surface;
  `docs/07-security-threat-model.md` is updated and residual risks are
  recorded.
- [ ] Multiplatform CI: the suite runs on Windows and Linux across the
  supported shells and packaging paths.
- [ ] Release candidates: an RC process produces tagged candidates with
  smoke gates (`api-sentinel doctor` green post-install).
- [ ] Documentation and onboarding are complete: install → first
  investigation journey documented per the §26 documentation set.
- [ ] Operational runbooks exist for backup/restore, connector failure,
  quarantine triage and budget exhaustion.
- [ ] Safe upgrade/downgrade: versioned migrations are tested in both
  directions on real workspaces; irreversible steps are documented.
- [ ] Contract version compatibility: a producer/consumer compatibility
  matrix for `api-sentinel/<Name>/v1` envelopes is approved.
- [ ] Offline functionality verified: the suite runs with network blocked
  except declared connector tests.

# Constraints

- Hardening only — no new subsystems, features or contracts introduced in
  this cycle without an ADR.
- §21.2 targets: ≥90% branch coverage on the deterministic core; essential
  decision and negative cases complete for security/contract modules; zero
  duplications on idempotent reprocessing fixtures; zero known-secret
  leakage; zero unit distortion; zero silent evidence loss.
- An unknown contract version is never accepted silently (invariant) — the
  compatibility matrix proves explicit rejection paths.
- Truncated evidence indicates loss; audit events remain append-only.
- Every previous invariant (§21.1) is exercised by at least one test in the
  final suite.
- External actions stay approval-gated; the read-only AWS posture is
  re-verified before release.
- Success requires a representative environment — not only the local lab.

# Review Notes

- Distinguish demonstrated criteria from aspirations: every gate item needs
  a run record, not a narrative.
- Review the threat-model delta with the owner before sign-off.
- Verify upgrade/downgrade evidence ran on real workspaces, including a
  populated one.
- Check the CI matrix actually covers Windows + Linux shells and the
  packaging path, not just Linux containers.
- Confirm self-observability indicators are honest (the Sentinel's own
  missing data must also degrade honestly).

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: the enterprise-oriented profile (external auth, RBAC,
  scalable storage), SaaS/hosted distribution, PyPI publication without
  explicit owner sign-off, multi-node or distributed deployment, new
  connectors.
- Riskiest assumption: the single-process SQLite topology survives the
  representative-environment benchmark without re-architecture.
- Smallest acceptable version: recorded benchmark + restore proof + authz
  verification + Windows/Linux CI + one tagged RC + the runbook set.
