---
id: cycle-08-agentic-investigation-change
title: "Cycle 8 — Agentic Investigation & Change Intelligence"
agent: claude
risk: high
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Adds Level-3 investigation: agentic sessions governed by the Agent Bridge,
Lens trace references, bounded context packs, and correlation of anomalies
with commits/deploys (`ChangeEvent`s). Governed by
`docs/06-integration-contracts.md` (Bridge/Lens/API Forge rows, handoff
rules), `docs/07-security-threat-model.md` (prompt-injection boundary,
governed execution), `docs/03-domain-model.md` (`ChangeEvent`,
`Investigation`, hypothesis confidence classes), blueprint §10.3, §12 and
§13.6, ADR-010 (sessions via Bridge), ADR-016 (Lens by reference).

_Depends: cycle-05-cross-forge-intelligence (handoff machinery, adapters),
cycle-04-detection-incident-lifecycle (anomalies/incidents to investigate)._

# Acceptance Criteria

- [ ] Agentic investigation sessions are created and governed by the Agent
  Bridge — the Sentinel builds no agent runtime; sessions preserve the
  authorized principal, workspace, provider selection, cost limits,
  approvals and history (ADR-010).
- [ ] Lens traces of agentic executions are referenced by trace/session id
  plus provenance; the Sentinel never stores a full trace copy (ADR-016).
- [ ] Context packs handed to agents are bounded in scope and size; no log,
  trace or API document is ever treated as trusted agent instruction
  (prompt-injection boundary, docs/07).
- [ ] `ChangeEvent` ingestion covers commits, releases, deployments,
  rollbacks, feature flags, configuration changes, gateway changes, auth
  policy changes, contract changes, dependency changes and maintenance
  events; GitHub Actions integration starts from exported pipeline
  manifests — no GitHub credential required.
- [ ] Change correlation uses a configurable temporal window plus plausible
  impact on operations/dependencies, version and environment, and
  independent evidence; LAB-004 associates a regressive deploy with its
  evidence — temporal proximity alone is never claimed as root cause.
- [ ] Hypotheses are explainable: each carries an explicit confidence class
  (`PROBABLE`/`POSSIBLE`/`UNVERIFIED`…) distinct from workflow state, and no
  uncalibrated numeric confidence score is fabricated.
- [ ] The anomaly status ladder
  `OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED` is enforced; a
  hypothesis becomes `VERIFIED` only when an independent tool or procedure
  supplies sufficient evidence.
- [ ] Verification plans recommend concrete next checks bound to authorized
  tools, with scope stated.
- [ ] Action approval: any external action requires an authorized scope and
  explicit approval; a Sentinel admin does not automatically obtain
  authorization to change external systems.
- [ ] API Forge handoff: problem + evidence + suggested change is delegated
  through the governed handoff path; the Sentinel never edits code in API
  Forge's place.
- [ ] Independent post-fix verification: after a fix, a separate
  check confirms indicator improvement — resolution is never self-declared.
- [ ] Bridge unavailable → deterministic investigations remain available;
  the degradation is a named state, not a failure.

# Constraints

- No own agent runtime (Bridge owns sessions); no second agent APM (Lens
  owns traces); no code edits in API Forge's place.
- An external action requires an authorized scope (invariant) — every
  mutation path is approval-gated and journaled.
- A closed incident cannot be modified without journaled history
  (invariant); verification outcomes append events.
- Secrets cannot appear in receipts or context packs (invariant) — packs
  pass redaction before leaving the workspace.
- Handoffs stay versioned; unknowns are preserved and never silently
  defaulted.
- AI interprets evidence and proposes verifications — it never invents
  anomalies and never replaces deterministic detection (Level-3 role,
  §10.3).
- Telemetry/logs/traces are untrusted data, never instructions to the
  agent.
- Correlation honesty: causal certainty requires evidence; temporal
  proximity alone is not root cause.

# Review Notes

- Trace a session end-to-end and verify governance attributes (principal,
  workspace, provider, cost limit, approvals) survive intact into the
  Bridge call.
- Check context-pack bounds are enforced mechanically (size caps, ref
  authorization), not by prompt wording.
- Verify the post-fix verifier is independent of the fixing path.
- Confirm LAB-004 evidence links are inspectable and the claim language
  stays at hypothesis level until verified.
- Check approval gates cannot be bypassed by agent output — actions execute
  only through the governed path.

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: building an agent runtime or a trace store, autonomous
  remediation without approval, DORA delivery-metrics dashboard (events are
  structured to permit it later), changes to Lens or Bridge internals.
- Riskiest assumption: the Agent Bridge session contract covers every
  governance attribute the Sentinel must preserve (principal, cost,
  approvals).
- Smallest acceptable version: one authorized agent session over a bounded
  evidence pack solving LAB-004 correlation, with an approval-gated action
  and independent post-fix verification.
