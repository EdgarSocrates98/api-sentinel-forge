# 17 — Release Readiness

## Development-start checklist (Cycle 1 gate prerequisites)

Items count as homologated only when the respective documents, decisions
and tests exist — this is a tracking instrument, not a checkbox page.

| # | Item | Evidence |
| --- | --- | --- |
| 1 | PRD and MVP scope approved | docs/01 |
| 2 | Ownership matrix without duplications | docs/06, capability-ownership |
| 3 | Essential architectural ADRs done | docs/adr/001–018 |
| 4 | Identity and observation model versioned | docs/03, contracts/v1 |
| 5 | Metric/unit semantics defined | docs/05, 08, 16 |
| 6 | Threat model and data policy established | docs/07 |
| 7 | Storage/migration/retention specified | docs/10 |
| 8 | Doctor/Atlas/The Forge contracts defined | docs/06, 16 |
| 9 | Portal UX flow drawn | docs/11 |
| 10 | E2E labs and oracles planned | docs/13 |
| 11 | CI/security/regression gates defined | docs/13, CI workflows |
| 12 | Explicit AI/cloud limits | docs/08 §L3, 07, MVP scope |

## Risk register

| Risk | Impact | Strategy |
| --- | --- | --- |
| Product too broad | High | vertical MVP with real cases |
| Doctor API duplication | **Critical** | ownership + boundary tests |
| API Operations confused with Product Forge | High | separate product inventory from runtime observability |
| Forge coupling | High | public contracts + adapters |
| Mathematically wrong metrics | **Critical** | oracles, histograms, temporality tests |
| LLM producing false cause | High | hypotheses separated from facts |
| Credentials/PII in evidence | **Critical** | minimization, authorization, redaction |
| CloudWatch cost | High | budgets + selective collection |
| Many features, immature UX | High | complete journeys before adding screens |
| Complicated install | High | simple CLI, profiles, standalone support |
| Missing real test data | High | instrumented labs |
| Missing retention policy | High | storage and lifecycle from day one |

The biggest product risk is not an inadequate library — it is building
observability, diagnosis, security, testing, APM, API management,
orchestration and autonomous fixing at once. The Sentinel orchestrates
this knowledge; it does not own every implementation.

## Success metrics

| Dimension | Indicator |
| --- | --- |
| Onboarding | time to first valid investigation |
| Integration | % sources correctly configured |
| Telemetry quality | complete windows, no ambiguous data |
| Diagnosis | % investigations with useful evidence |
| Reliability | lab incidents correctly detected |
| Security | leaks and unauthorized actions |
| UX | tasks completed without the CLI |
| Interoperability | compatible, verified handoffs |
| Maintainability | changes without regression across Forges |
| Performance | time, memory, volume per scenario |

**First-release headline metric:** go from a real observed error in a
lab API to an investigation with evidence, relevant dependencies, a
grounded hypothesis and a verifiable next action — without excessive
manual configuration.

## Release policy

Readiness criteria must exist as documents, decisions and tests — not
as checked boxes. Production-hardening claims wait for Cycle 9:
operational criteria demonstrated in a representative environment, not
only in the local lab. The enterprise-oriented profile is never
marketed as production readiness for the MVP.
