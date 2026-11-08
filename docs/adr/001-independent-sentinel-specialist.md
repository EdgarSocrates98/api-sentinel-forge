# ADR-001 — API Sentinel as an Independent Specialist

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

The FORGE ecosystem already divides responsibilities across eight
Forges. Temporal observation of running APIs and the operational
incident-investigation process have no natural owner: the Doctor owns
deterministic contract/config analysis, Atlas owns the structural graph,
Bridge owns agent sessions, The Forge owns routing.

## Decision Drivers

- Temporal observation + investigation lifecycle ≠ any existing Forge's
  exclusive responsibility.
- Adding a runtime to an existing specialist would blur its boundary
  (e.g., the Doctor must stay offline and deterministic — no
  scheduling, network or code changes).

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Independent specialist repo `api-sentinel-forge` | new bounded product, versioned contracts to siblings | clean ownership; respects existing boundaries |
| B — Fold the runtime into Forge Doctor API | Doctor gains observation + incidents | violates Doctor's offline/deterministic frontier; duplicates nothing but corrupts a working boundary |

## Decision

Create `api-sentinel-forge` as an independent specialist with a narrow
core: read connectors, derived metrics, baselines, anomaly detection,
incidents, temporal correlation and evidence references — without
rebuilding existing specialized engines.

## Consequences

**Positive**

- Clear capability ownership; the Doctor/Atlas boundaries stay intact.
- Sentinel can evolve detection/incident semantics independently.

**Negative**

- One more repo/distribution to maintain and integrate.
- Cross-forge features (Cycle 5+) depend on contract adapters landing.

## Compatibility

Sentinel publishes `api-sentinel/*/v1` contracts; consumes sibling
contracts only via anti-corruption adapters (ADR-006). No sibling needs
code changes in Cycle 0/1.

## Reversal Strategy

If the split proves wrong, the incident/observation core is already a
separate bounded context (`incidents`, `telemetry`) extractable or
mergeable behind the same versioned contracts — consumers would see a
deployment change, not a contract break. High cost; revisit only with
ownership-matrix evidence.

## Links

- Blueprint `prompt_inicial.md` §1, §2, §23 (ADR-001)
- `docs/06-integration-contracts.md`
