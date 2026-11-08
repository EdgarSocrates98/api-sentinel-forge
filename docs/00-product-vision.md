# 00 — Product Vision

**API Sentinel Forge — Observe. Correlate. Diagnose. Verify.**

Local-first platform for API Reliability, Runtime Intelligence & Incident
Investigation, natively federated in the FORGE ecosystem.

## The five questions the product answers

1. **What's happening?** Which services, routes and dependencies are
   healthy, degraded or down?
2. **What changed?** A deploy, a contract change, a config change, or
   abnormal behavior?
3. **Why might it be happening?** Which evidence supports hypotheses, what
   remains unknown, what's the impact?
4. **What can I do?** Recommended checks/fixes, which need approval, which
   FORGE specialists join in?
5. **How do I prove resolution?** Which indicators improved, which tests
   passed, what was recorded for audit?

## Positioning

- **Execution model**: local-first + opt-in connectors.
- **Main experience**: Forge Portal (API Operations).
- **Specialized diagnosis**: Forge Doctor API.
- **Orchestration & agents**: The Forge + Agent Bridge.

The product is not another dashboard, APM or API-test runner. It turns
telemetry, contracts, code, dependencies and deployment events into
reproducible operational investigations backed by existing specialists.

## Product capabilities (priority)

| Capability | Role | Priority |
| --- | --- | --- |
| API Inventory & Identity | services, operations, environments, versions | P0 |
| Telemetry Ingestion | receive observations from authorized sources | P0 |
| Runtime Health | indicators, quality, availability | P0 |
| SLO Intelligence | objectives and error budgets | P0 |
| Anomaly Detection | degradations via reproducible rules | P0 |
| Incident Lifecycle | open, track, close investigations | P0 |
| Dependency & Change Correlation | code, deploys, observed calls | P1 |
| Security & Contract Posture | Doctor results correlated | P1 |
| Agentic Investigation | governed AI investigation | P1 |
| Operations Center | Portal screens | P0 |

P0 = the product core; it does not mean all land in the first PR. The MVP
must work well before the number of integrations grows.

## Architectural decision

Independent repository `api-sentinel-forge` with a much smaller core than
the full product vision. The Sentinel's exclusive responsibility is
**observing running APIs over time and managing the operational
investigation process**. Everything else stays with its owning Forge.

## Success measure (first release)

Go from a real observed error in a lab API to an investigation with
evidence, relevant dependencies, a grounded hypothesis and a verifiable
next action — without excessive manual configuration.
