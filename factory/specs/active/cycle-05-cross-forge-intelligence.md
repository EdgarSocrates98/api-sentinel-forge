---
id: cycle-05-cross-forge-intelligence
title: "Cycle 5 — Cross-Forge Intelligence"
agent: claude
risk: high
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Wires the Sentinel into the FORGE ecosystem through anti-corruption adapters:
first forge-doctor-api and code-atlas-forge, then registration with the-forge
via `forge-knowledge/api-sentinel-forge.json` and a hand-built
`the-forge/adapters/apisentinel` adapter. Governed by
`docs/06-integration-contracts.md` (responsibility contract, two graphs,
handoffs, degradation contract), `docs/04-api-contracts.md`
(`api-sentinel/InvestigationHandoff/v1`), blueprint §13–§14, ADR-006
(versioned contracts), ADR-009 (handoffs via The Forge), ADR-012 (identity
mapping with provenance).

_Depends: cycle-04-detection-incident-lifecycle (investigations/incidents to
enrich), cycle-03-runtime-health-slo (operational observation fed into
correlation)._

# Acceptance Criteria

- [x] Doctor adapter: the Sentinel selects a bounded evidence package,
  resolves authorized refs, creates a versioned handoff and delegates;
  returned findings and unknowns are recorded as investigation evidence —
  never reclassified, never stripped of provenance.
- [x] Atlas adapter: structural entities/relations are consumed through the
  Atlas public contract; the structural graph (Atlas-owned) and the
  operational graph (Sentinel-owned `DependencyObservation`s) stay
  conceptually distinct — a structural edge never proves a call happened and
  an observed call never proves a code path.
- [x] Identity translation: canonical `service_id`/`operation_id` ↔
  `external_refs` mapped with provenance (ADR-012); unresolved identities
  stay `UNKNOWN` rather than name-matched guesses (LAB-006).
- [x] Versioned handoffs: `InvestigationHandoff/v1` is translated by the
  adapter to the effective `ForgeRequest`/`ForgeHandoff` contract of the
  target; unknown envelope versions are rejected explicitly.
- [x] Results carry unknowns end-to-end; unknowns are never silently
  converted into default values.
- [x] Every handoff produces a receipt; receipts contain no secrets and
  preserve the original errors and limitations of the response.
- [x] Per-integration timeouts are configured and bounded; a timeout yields a
  named terminal state, not a hang.
- [x] Unavailability handling: each integration is optional — absence
  produces a named refusal/degraded state (LAB-011), never a simulated
  execution or fabricated success.
- [x] Consumer/provider contract tests exist for the Doctor and Atlas seams
  on both sides of the envelope.
- [x] Correlated-diagnosis proof: the LAB-003 checkout cascade identifies the
  observed dependency correctly while avoiding unevidenced causal certainty;
  LAB-005 shows Doctor identifying the relevant contract change.
- [x] The-forge adapter (`adapters/apisentinel`, in the the-forge repo)
  declares `CapabilitySpec`s mapping Sentinel public seams to
  `api.runtime-observation`, `api.slo-assessment`, `api.anomaly-detection`,
  `api.incident-investigation`, `api.deployment-correlation` — with the
  verification of those ids against the live capability catalog recorded in
  the run evidence.
- [x] `forge-knowledge/api-sentinel-forge.json` ships a bootstrap package
  declaring operational facts only (`capabilities_source =
  "runtime_discovery"` — never runtime capabilities).
- [x] The Sentinel public seam is a typed boundary module (e.g.,
  `integrations/the_forge/boundary.py`) exposing `describe`, `health`,
  `capabilities` and `execute` over versioned envelopes; a discovered
  provider is never automatically considered executable
  (DISCOVERED ≠ VERIFIED).

# Constraints

- Sibling Forges are consumed through public versioned contracts only —
  never internal imports; the Sentinel never reimplements Doctor analysis,
  Atlas graphs or The Forge routing.
- The Doctor stays offline and deterministic; all external collection
  belongs to the Sentinel's governed connectors.
- A Doctor diagnosis must conserve its provenance (invariant).
- Secrets cannot appear in receipts (invariant).
- A discovered provider cannot be automatically considered executable
  (invariant).
- An unknown contract version cannot be accepted silently (invariant).
- An external action requires an authorized scope (invariant).
- Handoffs route through The Forge (ADR-009) — no alternative router is
  built here.
- Data Sentinel participation stays optional and domain-triggered
  (LAB-012); generic HTTP logs are never sent to a Spark analyzer just to
  include it.
- Every integration is optional: absence is a named state, never fabricated
  success (degradation contract, docs/06).

# Review Notes

- Check adapters translate foreign models at the boundary — no foreign
  internals leak into domain types, and no Sentinel engine duplicates a
  specialist's engine.
- Verify evidence packages are bounded (size-limited, authorized refs only).
- Confirm the two-graph distinction survives in the correlation layer.
- Look for the recorded live-catalog check of the five capability ids —
  semantic collisions must be flagged to the owner, not worked around.
- Verify timeout and unavailability tests produce named states, not generic
  exceptions.

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: Agent Bridge sessions and Lens trace references (Cycle 8),
  AWS connectivity (Cycle 7), mandatory Data Sentinel integration, MCP
  server surface, Portal UI (Cycle 6).
- Riskiest assumption: the five candidate capability ids match the live
  the-forge catalog without semantic collisions.
- Smallest acceptable version: Doctor + Atlas adapters with versioned
  handoffs, receipts, timeouts and the LAB-003 lab proof — plus the-forge
  adapter and the forge-knowledge registration file.
