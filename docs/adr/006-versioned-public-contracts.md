# ADR-006 — Versioned Public Contracts (No Internal Imports)

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Sentinel consumes Doctor findings, Atlas relations, The Forge routing
and Bridge sessions. Reaching into sibling repos' internals would weld
the ecosystem together and break on every refactor.

## Decision Drivers

- Forges evolve independently; boundaries must survive version drift.
- The blueprint mandates: module calls implemented by public contracts,
  never arbitrary cross-repo imports.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Versioned JSON envelopes + typed Python models; ACL adapter per Forge | schemas `api-sentinel/*/v1`; adapters translate foreign models at the boundary | explicit evolution rules; isolation |
| B — Direct imports of Forge internals | `import sparkforge_…`-style coupling | every sibling refactor breaks Sentinel |

## Decision

Versioned public contracts everywhere. Evolution rules: optional
additive fields may keep the version; meaning changes require a new
version; units explicit; incompatible versions rejected explainably;
unknowns never become defaults; evidence size-bounded; refs resolved
under authorization; errors/limitations preserved end-to-end.

## Consequences

**Positive**

- Consumer/provider contract tests pin compatibility (LAB-005,
  engineering target: schema matrix).
- Unknown versions are rejected, never silently defaulted (invariant).

**Negative**

- Adapter + schema maintenance cost per integration.
- Translation layers can lag upstream features by design.

## Compatibility

This *is* the compatibility surface: `contracts/v1/*.schema.json`
governs what Sentinel publishes; consumed contracts are pinned per
adapter (`forge_doctor_api >=0.2.0,<0.3.0`, see docs/16).

## Reversal Strategy

Contracts are append-versioned; dropping an adapter removes the
dependency without touching the core. Moving to internal imports would
require breaking the degradation contract — not recommended.

## Links

- Blueprint `prompt_inicial.md` §5, §13, §14.4, §23 (ADR-006)
- `docs/04-api-contracts.md`, `docs/06-integration-contracts.md`
