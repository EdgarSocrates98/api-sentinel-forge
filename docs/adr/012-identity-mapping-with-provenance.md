# ADR-012 — Identity Mapping with Provenance

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-11-08 |

## Context and Problem

The same API appears under different names in telemetry sources, cloud
accounts, OpenAPI documents and the Atlas graph. The friendly service
name cannot be the unique identity — two `payments-api` in different
accounts are different services.

## Decision Drivers

- Mis-merged identity corrupts metrics, incidents and handoffs.
- Every association between identity kinds needs origin + evidence
  (who asserted it, based on what).

## Options

| Option | Summary | Assessment |
| --- | --- | --- |
| A — Canonical identity + provenance-tracked mapping between logical/deployment/observation identities | `api-sentinel/ServiceIdentity/v1` with `external_refs` | disambiguates twins; auditable joins |
| B — Match by name only | merge records sharing a label | silent false merges; unfixable history |

## Decision

Three identity kinds — **logical** (stable concept), **deployment**
(region, cluster, account, stage, version, instance) and
**observation** (how a source labels it) — associated only with
provenance and evidence. Deployment version does not compose logical
identity but remains available for segmentation and release
comparison. Unmapped routes stay `UNKNOWN` (LAB-006), never guessed.

## Consequences

**Positive**

- Correlation between structural (Atlas) and operational (Sentinel)
  graphs happens over resolved identities only.
- Wrong merges are diagnosable from provenance records.

**Negative**

- Onboarding asks for environment/namespace context.
- Identity resolution logic must handle partial refs honestly.

## Compatibility

`ServiceIdentity/v1` is a published contract; consumers must not
assume `service_name` uniqueness.

## Reversal Strategy

Mappings are data, not code: a bad association is corrected by a new
provenanced mapping record plus investigation note — original rows
stay for audit. Reverting to name-matching would silently corrupt
identity; not supported.

## Links

- Blueprint `prompt_inicial.md` §6.1, §23 (ADR-012)
- `docs/03-domain-model.md`, `contracts/v1/service_identity.schema.json`
