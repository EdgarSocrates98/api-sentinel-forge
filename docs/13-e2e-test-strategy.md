# 13 — E2E Test Strategy

The product must prove capabilities over reproducible situations, not
assert them. Labs live under `lab/scenarios/`; oracles check both the
expected detection and the absence of overclaiming.

## Lab scenarios

| Scenario | Injected failure | Required proof |
| --- | --- | --- |
| LAB-001 | elevated latency | detection per operation |
| LAB-002 | persistent HTTP 503 | error ratio, incident opened, recovery observed |
| LAB-003 | dependency failure | call correlation and origin identification |
| LAB-004 | regressive deploy | change association and evidence |
| LAB-005 | incompatible contract | Doctor API identifies the relevant change |
| LAB-006 | unmapped route | identity stays UNKNOWN |
| LAB-007 | incomplete source | Sentinel does not fabricate health |
| LAB-008 | sampled traces | global rates not incorrectly inferred |
| LAB-009 | duplicated data | idempotent ingestion |
| LAB-010 | credentials in logs | no known secret persisted |
| LAB-011 | provider unavailable | honest functional degradation |
| LAB-012 | stale data | Data Sentinel participates when applicable |
| LAB-013 | AWS throttling | backoff, pagination, budget |
| LAB-014 | restart recovery | consistent state and history |
| LAB-015 | SLO change | preserved revision, reproducible calculation |
| LAB-016 | unauthorized user | access denied without leakage |

### Flagship lab — LAB-003 checkout cascade

Topology: checkout API → orders service → payments service → controlled
database dependency. The failure is injected in **one** component only.
The Sentinel receives observations, identifies rising errors, opens an
incident and gathers observed relations; then Code Atlas supplies
structural relations and the Doctor evaluates relevant artifacts.

The oracle verifies the dependency was identified correctly **and** that
no causal certainty was attributed without sufficient evidence. This is
one of the strongest tests of the whole proposal.

## Quality pyramid — per domain, not a generic test count

| Layer | What it proves |
| --- | --- |
| Unit | calculations, rules, invariants |
| Property-based | behavior under input combinations |
| Contract | schemas, compatibility, evolution |
| Integration | real DB, adapters, CLIs |
| E2E | service, ingestion, incident, Portal |
| Negative | failures and limits |
| Security | SSRF, credentials, paths, authorization |
| Performance | throughput, memory, time |
| Recovery | restart, migration, backup, restore |
| Cross-platform | Windows/Linux, shells, packaging |

## Non-negotiable invariants

- An idempotent batch never duplicates points.
- Unsampled data is never labeled sampled, nor the inverse.
- A missing source never yields fabricated positive health.
- Percentiles are never combined by simple average.
- Counter resets never produce negative rates.
- Truncated evidence declares the loss.
- An unknown contract version is never silently accepted.
- A closed incident is never modified without history.
- An external action requires authorized scope.
- Secrets never appear in receipts.
- A discovered provider is not automatically executable.
- A Doctor diagnosis keeps its provenance.
- A rule change never rewrites historical results without explicit
  revision.

## Engineering targets (proposed — subject to benchmark validation)

| Metric | Initial target |
| --- | --- |
| Branch coverage, deterministic core | ≥ 90% |
| Security/contract critical modules | essential decision and negative cases complete |
| Idempotent reprocessing | zero duplications on fixtures |
| Known-secret leakage | zero |
| Unit distortion | zero |
| Silent evidence loss | zero |
| Small local import | time measured and documented, no artificial threshold |
| DB recovery | approved restore scenario |
| Offline functionality | tests with network blocked |
| Schema compatibility | producer/consumer matrix approved |

No events-per-second ingestion target is set without benchmarks on the
target hardware.
