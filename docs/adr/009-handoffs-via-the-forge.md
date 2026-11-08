# ADR-009 — Handoffs Routed via The Forge

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

Sentinel investigations delegate bounded work to specialists (Doctor
diagnosis, Atlas structure, later Bridge sessions). Someone must decide
WHO runs a handoff; The Forge already owns discovery, routing and
governance between specialists.

## Decision Drivers

- Duplicating routing inside Sentinel creates a second, weaker
  governance plane (explicitly forbidden in the responsibility
  contract).
- Deterministic routing without LLM in the core is already The Forge's
  model; ambiguity becomes `ambiguous`, never a guess.

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Handoffs via The Forge; adapter translates `InvestigationHandoff` → `ForgeRequest`/`ForgeHandoff` | Sentinel declares intent + context; Forge routes + receipts | single governance plane; honest degradation when absent |
| B — Sentinel's own router | direct sibling calls per config | alternative routing = duplicated governance; bypasses receipts |

## Decision

All specialist handoffs go through The Forge. The
`api-sentinel/InvestigationHandoff/v1` envelope is Sentinel-internal
intent; an adapter translates it to the contract The Forge actually
supports — it does not replace `ForgeRequest`/`ForgeHandoff`.

## Consequences

**Positive**

- Receipts, authorization and execution policy live in one place.
- Sentinel works standalone; with The Forge absent, handoffs degrade
  to a named state, not fake execution.

**Negative**

- Full investigation flow depends on the Cycle 5 adapter
  (`the-forge/adapters/apisentinel`) landing.
- Capability ids must be verified against the live catalog to avoid
  semantic collisions.

## Compatibility

Sentinel registers capabilities `api.runtime-observation`,
`api.slo-assessment`, `api.anomaly-detection`,
`api.incident-investigation`, `api.deployment-correlation` — ids
provisional until catalog verification.

## Reversal Strategy

Because all routing is concentrated behind one adapter + one envelope
contract, an emergency direct-call mode could be added per integration
without changing investigation semantics — but it would forfeit
receipts; keep as contingency, not default.

## Links

- Blueprint `prompt_inicial.md` §13.5, §14.3, §23 (ADR-009)
- `docs/06-integration-contracts.md`, `docs/16-compatibility-matrix.md`
