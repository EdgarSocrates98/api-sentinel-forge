# ADR-011 — API Operations Lives in the Forge Portal

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

The visual experience needs a home. Forge Portal already has an
`/api-operations` area fed by `PfApi` (Product Forge) showing product
inventory/contracts/health — a good visual entry but not an operational
observability center.

## Decision Drivers

- One integrated visual experience is a stated goal; a competing
  frontend is forbidden by the responsibility contract.
- The existing screen consumes product-inventory data; runtime
  observability is a different domain that must not be conflated
  (risk: API Operations vs Product Forge confusion).

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Evolve the Portal's API Operations area onto Sentinel APIs | Sentinel exposes a local `/v1` surface; Portal renders it | federated experience; no second frontend |
| B — Standalone Sentinel frontend | own SPA/app | duplicate UI stack; splits the operator journey |

## Decision

The API Operations area of the Forge Portal evolves to consume the
Sentinel's local API. MVP screens: Overview, Service Detail,
Investigation. Integration with the Product Forge experience happens
only where correspondence is proven (Cycle 6 gate).

## Consequences

**Positive**

- User starts from an API + problem, not from choosing a Forge.
- Portal down never breaks CLI/local API (failure strategy).

**Negative**

- UX depends on another repo's release cadence.
- Identity mapping between product inventory and runtime services is
  real work (ADR-012).

## Compatibility

Sentinel publishes `GET /v1/services`, `/metrics`, `/slos`,
`/incidents`, `/investigations`, `/evidence`, `/integrations/status`
for this surface (docs/04). Portal binds localhost.

## Reversal Strategy

The local HTTP API is the seam: a different consumer (or a future
standalone UI) could attach without domain changes. Reverting to
product-inventory-only view means dropping Sentinel widgets — data
model unaffected.

## Links

- Blueprint `prompt_inicial.md` §2, §18, §23 (ADR-011)
- `docs/11-portal-ux-design.md`, `docs/04-api-contracts.md`
