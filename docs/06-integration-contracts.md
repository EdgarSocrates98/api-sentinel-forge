# 06 — Integration Contracts

The Sentinel integrates with the eight Forges through versioned public
contracts only — never internal imports. An anti-corruption adapter per
Forge translates foreign models at the boundary.

## Responsibility contract

| Forge | Input to them | Output expected | Sentinel may never |
| --- | --- | --- | --- |
| api-forge | problem, evidence, suggested change | plan, implementation or proposal | edit code in its place |
| forge-doctor-api | bounded evidence package | findings, unknowns, diagnostics | reimplement its analyzers |
| code-atlas-forge | repo, snapshot, identity | structural entities/relations | build another code graph |
| the-forge | capability, context, handoff | routing, execution, receipt | create alternative routing |
| forge-agent-bridge | session + authorized context | agentic execution events | build its own agent runtime |
| forge-lens | trace/session id | agentic execution evidence | build a second agent APM |
| data-sentinel-forge | data-problem reference | pipeline/workload diagnosis | interpret Spark directly |
| forge-portal | query/operation APIs | interface and navigation | build a competing frontend |

## Membership artifacts (per forge-bootstrap)

- `forge.json` / `forge.agentic.json` — specialist manifests (done).
- `forge-knowledge/api-sentinel-forge.json` — bootstrap package for
  `the-forge` (shipped here for review; copied into the-forge at
  registration). It declares operational facts only — never runtime
  capabilities (`capabilities_source = "runtime_discovery"`).
- `the-forge/adapters/apisentinel` — hand-built adapter (Cycle 5):
  declares `CapabilitySpec`s mapping public seams → capabilities
  (`api.runtime-observation`, `api.slo-assessment`,
  `api.anomaly-detection`, `api.incident-investigation`,
  `api.deployment-correlation` — ids verified against the live catalog at
  integration time).
- Sentinel public seams (Cycle 5 design target): a typed boundary module
  (e.g., `integrations/the_forge/boundary.py`) exposing `describe`,
  `health`, `capabilities` and `execute` over versioned envelopes — the
  same seam shape `forge_doctor_api.handoff.boundary` exposes today.

## Two different graphs

- **Structural** (Code Atlas owns): relations found in code/contracts/IaC.
- **Operational** (Sentinel owns): observed calls, failures, latencies.

Correlation associates the two by resolved identities and evidence. A
structural edge does not prove a call happened; an observed call does not
prove a code path.

## Handoffs

- Sentinel → Doctor: selects compatible evidence, resolves authorized
  refs, creates a versioned handoff, delegates. Findings+unknowns come
  back recorded as investigation evidence — never reclassified.
- Sentinel → The Forge: the `InvestigationHandoff` envelope is translated
  by an adapter to the effective `ForgeRequest`/`ForgeHandoff` contract.
- Sentinel → Bridge: agentic sessions are created and governed by Bridge
  preserving principal, workspace, provider choice, cost limits,
  approvals and history. No log/trace/doc is trusted agent instruction.
- Sentinel → Lens: references traces by id+provenance; never stores a
  full copy.
- Sentinel → Data Sentinel: optional, domain-triggered (e.g., stale-data
  API correlated to a pipeline). Generic HTTP logs are never sent to a
  Spark analyzer just to include it.

## Degradation contract

Every integration is optional: absence produces a named refusal/state —
never a simulated execution or fabricated success.
