# Cycle 11 — Planning Only

Cycle 11 is intentionally not implemented by Cycle 10.3. The proposals below
reuse the capabilities already owned by the Forge ecosystem and require a
separate authorization and design review.

## Pillar A — Advanced API Observability

| Field | Proposal |
|---|---|
| capability | Release comparison, latency regression detection and SLO burn-rate forecasting |
| user_problem | Operators see an incident after degradation but cannot quickly compare it with the last release or forecast error-budget exhaustion. |
| existing_owner | API Sentinel Forge |
| existing_functionality | Telemetry ingestion, SLI/SLO evaluation, anomaly detection, incidents, investigations and evidence receipts. |
| gap | No governed release-to-release baseline or forward-looking burn-rate projection is part of the current operational contract. |
| proposed_improvement | Add read-only comparison and forecast reports that consume existing observations, releases, SLOs and evidence; no new ingestion engine. |
| integration | Feed Code Atlas dependency identities into context only through the existing structural adapter; publish traces through Forge Lens when configured. |
| risk_of_duplication | Medium; must not duplicate Code Atlas graph analysis or Forge Lens trace storage. |
| complexity | Medium |
| expected_value | Faster regression triage and earlier error-budget decisions. |
| dependencies | Existing Sentinel observations/SLOs; optional Atlas/Lens references; no mandatory cloud provider. |
| negative_tests | Sparse windows, clock skew, missing release, malformed SLO, unknown classification, duplicate observations and insufficient evidence. |
| e2e_acceptance | A fixed fixture with two releases produces a deterministic comparison, forecast and receipt; unavailable peers remain explicit BLOCKED. |
| priority | P1 |

## Pillar B — Agentic Incident Intelligence

| Field | Proposal |
|---|---|
| capability | Evidence-aware governed investigation workspace |
| user_problem | Investigators must manually carry evidence, hypotheses and approvals between incident work, specialist routing and agent sessions. |
| existing_owner | Forge Agent Bridge + Forge Lens + The Forge, with API Sentinel as incident owner |
| existing_functionality | Sentinel investigations and evidence, The Forge routing/receipts, Bridge sessions/approvals, Lens trace references. |
| gap | The existing boundaries do not yet provide one cross-product investigation view that enforces evidence provenance at every agent action. |
| proposed_improvement | Define a versioned read-only investigation context pack and approval checkpoints over the existing Bridge, Lens and The Forge contracts. |
| integration | Sentinel supplies incident/evidence refs; The Forge routes; Bridge authorizes; Lens records trace refs. |
| risk_of_duplication | High; do not create a second agent runtime, router, approval engine or trace store. |
| complexity | High |
| expected_value | Safer assisted diagnosis with auditable human approvals and reproducible reasoning inputs. |
| dependencies | Stable contracts in Agent Bridge, Lens and The Forge; optional provider credentials. |
| negative_tests | Revoked session, stale evidence, cross-workspace ref, unverified hypothesis, missing approval, prompt injection and provider timeout. |
| e2e_acceptance | A local provider-fake contract test proves refusal before unauthorized execution and a receipt links incident, evidence, approval and trace; real providers remain separately labeled. |
| priority | P1 |

## Pillar C — Operational UX

| Field | Proposal |
|---|---|
| capability | Incident timeline and guided diagnosis across TUI and Portal |
| user_problem | Operators need one focused timeline for traffic, SLO state, anomaly, incident, investigation and recovery without memorizing command flags. |
| existing_owner | Forge Portal for web UX; API Sentinel TUI for local UX |
| existing_functionality | Portal API Operations views, Sentinel CLI/TUI, incident lifecycle, SLO panels and evidence-backed investigation. |
| gap | The views are not yet a unified timeline and guided flow with consistent accessibility and PT-BR/EN terminology. |
| proposed_improvement | Compose existing API endpoints and receipts into a navigable timeline and guided actions; keep mutation approval and data ownership in the existing services. |
| integration | Portal consumes Sentinel APIs; TUI consumes the same application ports; Forge Lens remains the trace owner. |
| risk_of_duplication | Medium; no new portal backend, incident engine or observability store. |
| complexity | Medium |
| expected_value | Lower onboarding cost and fewer operator errors during incidents. |
| dependencies | Existing Portal build, Sentinel API contracts, accessibility review and localization resources. |
| negative_tests | Missing workspace, expired auth, secret-bearing output, markup/ANSI injection, narrow terminal, keyboard-only navigation and partial API responses. |
| e2e_acceptance | Browser and terminal smoke tests render the same incident timeline from a fixed receipt set, preserve redaction, and refuse unauthorized actions. |
| priority | P2 |

## Planning constraints

Before implementation, each proposal needs an owner-approved contract, a
duplication review against the current peer capabilities, negative tests and a
federated acceptance plan. No Cycle 11 code, schema, workflow or release is
included in Cycle 10.3.
