---
id: cycle-06-portal-api-operations
title: "Cycle 6 — Portal API Operations"
agent: claude
risk: medium
verification:
  - "python -m pytest -q"
  - "python -m ruff check ."
  - "python -m mypy src"
---

# Context

Exposes the Sentinel's use cases over a minimal local HTTP surface and
evolves the API Operations area inside the Forge Portal — no standalone
frontend (ADR-011). Governed by `docs/04-api-contracts.md` (endpoint table,
envelope rules), `docs/07-security-threat-model.md` (local HTTP trust
boundary, capability permission model), blueprint §15 (surface) and §18
(information architecture, UX principles).

_Depends: cycle-03-runtime-health-slo (metrics/SLOs to display),
cycle-04-detection-incident-lifecycle (incidents/timeline),
cycle-05-cross-forge-intelligence (specialist availability states)._

# Acceptance Criteria

- [ ] Local HTTP surface implements the minimum endpoint set:
  `/v1/health`, `/v1/capabilities`, `/v1/services`, `/v1/services/{id}`,
  `/v1/services/{id}/operations`, `/v1/services/{id}/metrics`,
  `/v1/services/{id}/slos`, `/v1/ingestion/jobs`,
  `/v1/ingestion/jobs/{id}`, `/v1/anomalies`, `/v1/incidents`,
  `/v1/incidents/{id}`, `/v1/incidents/{id}/timeline`,
  `/v1/incidents/{id}/investigations`, `/v1/investigations/{id}`,
  `/v1/evidence/{id}`, `/v1/integrations/status`.
- [ ] No generic `/execute`-style endpoint capable of invoking arbitrary
  commands exists.
- [ ] Import/investigation initiation has scoped authorization, concurrency
  limits, idempotency, audit records and asynchronous responses where
  appropriate.
- [ ] Portal shows the list of observed services with coverage indicators
  from real inventory — not demo data.
- [ ] Per-operation detail renders indicators computed from real ingested
  series (Cycle 3 outputs).
- [ ] Time-series charts carry explicit units and period, and mark sampling
  and data gaps visibly.
- [ ] SLO screens show objectives, budgets and windowed history including the
  revision that produced each assessment.
- [ ] Incident screens list active/historical incidents and render the
  investigation view: timeline, clickable evidence links to each artifact's
  origin, hypotheses with limitations.
- [ ] Availability states are honest — `AVAILABLE`, `UNAVAILABLE`,
  `PARTIAL`, `UNVERIFIED`; no arbitrary global health score (no "health
  87%") and no fabricated health when a source is missing.
- [ ] Product Forge experience integration activates only when a proven
  identity correspondence exists — never on name matching.
- [ ] Permission model is enforced by capability (Viewer/Analyst/Operator/
  Admin per docs/07): unauthorized requests are denied without information
  leakage (LAB-016 groundwork).
- [ ] Browser E2E proves the journey: a user investigates an API through
  Overview → Service Detail → Investigation without touching the CLI.
- [ ] Portal down → CLI and local API keep working; Sentinel down states are
  shown rather than cached fiction.

# Constraints

- Localhost bind by default; no internet exposure (docs/07).
- The Sentinel never builds a competing frontend — Portal owns navigation;
  the API exposes only what the use cases need, not internals.
- A missing source cannot produce fictitious positive health (invariant) —
  UI states must surface `UNVERIFIED`/`PARTIAL` honestly.
- An external action requires an authorized scope (invariant); actions show
  preview, scope and authorization before submission.
- All displayed payloads pass redaction at persistence plus additional
  redaction at the presentation layer.
- UX requirements: keyboard navigation, semantic states, responsive layout,
  PT-BR/EN internationalization, dark/light mode compatible with the Portal.
- Contracts stay versioned (`api-sentinel/<Name>/v1`); the wire uses schemas,
  internals use typed models.

# Review Notes

- Check every rendered number traces to a real query path — no mock layers
  left in production code.
- Verify filters by environment, period, service and operation, and
  before/after comparison around changes where data exists.
- Confirm the investigation export works and preserves evidence refs.
- Boundary: `api` context is a thin surface over application use cases — no
  business logic in handlers.
- Confirm the Product Forge correspondence check has evidence behind it
  before the integration activates.

# Grill Gate

- Owner: Edgar (repo owner)
- Out of scope: the full ten-screen map (MVP prioritizes Overview, Service
  Detail, Investigation), a standalone frontend, enterprise external
  auth/RBAC, real-time push updates, admin configuration screens beyond
  Settings basics.
- Riskiest assumption: the Forge Portal's extension model supports the API
  Operations area without forking the Portal.
- Smallest acceptable version: local HTTP API + Portal service list/detail/
  investigation over lab data, with one browser E2E journey green.
