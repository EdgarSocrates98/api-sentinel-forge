---
cycle: 10
deliverable: Code Atlas integration — real index-store seam (F011 closed)
wave: 2
---

# Cycle 10 — Code Atlas integration (Wave 2: REAL)

## What changed in Wave 2

- **C10-F011 FIXED.** The orphan `api-sentinel/AtlasExport/v1` contract is no
  longer the only seam. Code Atlas 1.1.1's real export surface is its
  **SQLite index** (`atlas.db` — `repositories`, `snapshots`,
  `domain_entities`, `domain_entity_occurrences`, `domain_relations`,
  `evidence`; every CLI read command wraps it). New
  `integrations/atlas/store_adapter.py::DbStructuralContext` consumes that
  store **read-only** (`sqlite3 mode=ro`), selected by
  `integrations.atlas.db_path` + optional `repository` (identity_key) /
  `snapshot_id`. It implements `StructuralContextPort` — the same port the
  file adapter serves — so `Correlate` needs no Atlas-specific code.
- `load_structural()` precedence: `db_path` → `export` file → `None`.
  The `AtlasExport/v1` FILE seam stays supported for pre-exported manifests;
  `lab/fixtures/atlas-export.json` remains a test fixture, not the proof path.
- `integrations` status now probes the db seam (`mode: db`) with the same
  selectors — `available` only when a real READY snapshot resolves.

## Real homologation

```text
code-atlas index lab/fixtures/atlas-repo --domains api
  → snapshot_id 11 · status READY · 4 api-domain entities · 9 domain
    relations (incl. 2 external + 1 unresolved CALLS_ENDPOINT with
    resolution_status/confidence/resolver/evidence preserved)
```

`correlate svc-gateway` then resolved the service's declared
`code_atlas_identity → service.app#app` and produced **3 structural
CONTAINS edges** (`→ GET /orders`, `→ POST /orders`, `→ GET
/orders/{order_id}`) — separate from observed edges, with the standing
limitation `correlation is not causation`. Evidence:
`evidence/atlas/index_run.json`, `evidence/atlas/domains_entities.json`,
`evidence/atlas/status.json`, `evidence/atlas_correlate_run.json`, and the
routed run `evidence/theforge/runs/20261109T132216Z-004a614a/`.

## Honesty rules enforced by the adapter

- **Never binds by name alone** — resolution keys on `code_atlas_identity`
  external ref → exact `identity_key`/`qualified_name`; no match → named
  unresolved (`structural_unavailable` / unknowns), never a guess.
- **Ambiguous repository** (no selector, >1 repo) → `IntegrationRefused`.
- **No READY snapshot** → refused — never silently serves a stale/FAILED one.
- Edges keep Atlas's own `type`/`resolution_status`/`confidence_class`/
  `resolver`/`evidence_ids` verbatim; unresolved and external targets stay
  unresolved/external — inferred is never promoted to observed.
- Read-only connection; the adapter never writes to `atlas.db`.

## Negative battery (`tests/unit/test_integrations_atlas_store.py` — 10 tests)

missing db · malformed db (missing tables) · ambiguous repository without
selector · explicit repository selector · no READY snapshot · unknown
snapshot_id · unresolved service identity → named state · external /
unresolved edges preserved · observed-vs-structural separation in Correlate.
