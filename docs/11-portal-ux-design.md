# 11 — Portal UX Design (API Operations Center)

The primary experience lives in the Forge Portal **API Operations** area
(ADR-011). The user does not start by choosing "which Forge to run" —
they select an API and describe a problem. Specialist selection is
invisible in the main journey but inspectable on demand.

## Navigation

| Screen | Purpose |
| --- | --- |
| Overview | service health at a glance |
| Services | inventory and coverage |
| Service Detail | operations, metrics, dependencies |
| SLOs | objectives, budgets, history |
| Incidents | active and historical incidents |
| Investigation | evidence, hypotheses, checks |
| Dependencies | correlated operational graph |
| Changes | deployment timeline |
| Sources | connector integrity and state |
| Settings | limits, policy, preferences |

All ten screens are not built at once. **MVP priority: Overview,
Service Detail, Investigation.**

## Central screen — investigation

Reference layout (data entirely fictional; real implementation consumes
published contracts):

```
API OPERATIONS / INVESTIGATIONS
Payments API · Latency Investigation          DEGRADED

p95: 890 ms      5xx: 3.5%      Evidence: 12

[ Observed latency chart — p95/p99, explicit units and time axis ]

Associated signals:  version change observed; dependency with timeout
Specialists:         Doctor API: available · Code Atlas: available
Next suggested action: compare runtime evidence with contract and
structural snapshot — approval required before any external change
```

## UX principles

- Reliable states: `AVAILABLE | UNAVAILABLE | PARTIAL | UNVERIFIED`.
- Links to every evidence artifact and its origin.
- Filters by environment, period, service, operation.
- Before/after comparison around changes.
- Charts with explicit units and time period.
- Sampling and gap indicators on every series.
- Clickable timeline.
- Actions with preview, scope and authorization state.
- Hypothesis explanations with explicit limitations.
- Investigation export.
- Keyboard accessibility; responsive layout.
- i18n PT-BR/EN; dark/light mode compatible with the Portal.

## No arbitrary health score

No global "87% healthy" indicator as primary signal. Show SLOs,
relevant metrics and observed sources with explainable criteria —
independent status semantics, not one universal score (ADR-018).
