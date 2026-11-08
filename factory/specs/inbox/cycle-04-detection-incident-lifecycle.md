---
id: cycle-04-detection-incident-lifecycle
title: "Cycle 4 — Detection & Incident Lifecycle"
agent: claude
risk: medium
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Builds the `detection` and `incidents` bounded contexts: a deterministic
detector registry (Level-1 rules plus simple baselines), anomaly dedup and
grouping, and the incident state machine with an append-only journal.
Governed by `docs/08-slo-and-detection-design.md` (detector protocol,
multi-window alerting rules), `docs/03-domain-model.md` (`DetectionRule`,
`Anomaly`, `Incident`, `incident_event`), CONTEXT.md (status ladder,
severity ladder), blueprint §10–§11, ADR-007 (deterministic detector) and
ADR-008 (current state + journal, not event sourcing).

_Depends: cycle-03-runtime-health-slo (indicators feeding rules),
cycle-02-telemetry-ingestion (observations, source liveness)._

# Acceptance Criteria

- [ ] Detector registry holds versioned detectors implementing the protocol
  (`detector_id`, `detector_version`,
  `evaluate(request, context) → DetectionResult`) with typed results carrying
  explicit evidence and limitations.
- [ ] Level-1 threshold rules implemented: error rate over limit, p95 over
  latency objective, source stopped sending observations, accelerated
  error-budget consumption, timeout growth after a deploy, unexpected status
  on an operation.
- [ ] Simple baselines (median/MAD or EWMA, window comparison) available as
  detector inputs; each is evaluated on a known corpus and never claims to
  identify root cause.
- [ ] Per-rule cooldown plus hysteresis against flapping; alert frequency is
  limited and planned suppression supported.
- [ ] Deduplication groups anomalies into one incident on service+operation,
  time window, detector signature, possibly-shared dependency, environment
  and release version; original anomalies are preserved with a recorded
  justification for the grouping.
- [ ] Incident lifecycle states implemented with operational status kept
  independent from confidence classification (e.g., `INVESTIGATING` +
  hypothesis `PROBABLE` does not imply proven cause).
- [ ] Severity model `SEV1..SEV4 + INFORMATIONAL` configured from business
  impact per service — never derived from HTTP status codes alone.
- [ ] Timeline: every state change appends an `incident_event` journal entry;
  first_observed/detected/acknowledged/resolved timestamps tracked.
- [ ] Reopen: a resolved incident can be reopened only via a journaled event;
  a closed incident is never edited without history.
- [ ] Escalation is defined by versioned rules, recorded with the rule
  revision that fired it.
- [ ] Incident persistence + audit: current-state tables plus journal
  (ADR-008); `revision` field enforces concurrency control; audit events are
  append-only.
- [ ] LAB-001: elevated latency is detected per operation. LAB-002:
  persistent HTTP 503 produces an error-ratio breach, an incident and a
  detected recovery — with zero duplicate incidents under event replay.

# Constraints

- Detector purity: no network, no real clock (injected), no credential
  access, no implicit persistence, deterministic tests, and no execution of
  arbitrary user-submitted rules.
- A closed incident cannot be modified without journaled history
  (invariant).
- Altering a rule must not rewrite historical results without an explicit
  revision (invariant).
- Missing traffic is treated separately from failure — a source that stops
  sending is its own detection case, never proof of service outage.
- Level-1 deterministic rules come first (ADR-007); no AI/LLM invents
  anomalies anywhere in this cycle.
- Anomaly status follows `OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED`;
  hypotheses carry confidence classes distinct from workflow state.
- Incident store is current-state tables + journal — not full event
  sourcing (ADR-008).

# Review Notes

- Verify the detector protocol is actually pure (no I/O reachable from
  `evaluate`).
- Check dedup justifications are stored and inspectable, not just grouping
  keys.
- Confirm severity never falls out of status-code counting.
- Replay the detection corpus twice and diff: identical anomalies, identical
  incidents, zero duplicates.
- Check every alert explains the calculation that fired it and records the
  rule version.

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: Level-3 agentic investigation (Cycle 8), cross-forge
  correlation via Doctor/Atlas (Cycle 5), notification channels, automatic
  remediation, seasonality analysis beyond the listed baselines.
- Riskiest assumption: threshold defaults and simple baselines detect the lab
  failures without per-service tuning.
- Smallest acceptable version: registry + threshold rules + dedup + incident
  lifecycle with journal, passing LAB-001/LAB-002 with zero duplicate
  incidents.
