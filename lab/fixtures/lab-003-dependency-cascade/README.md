# LAB-003 — dependency-cascade (checkout → orders → payments → db)

Reproduces the flagship cascade from `docs/13-e2e-test-strategy.md`:
a single injected failure — `payments`' datastore (`payments-db`) starts
timing out — propagates up the call chain as observed errors on
`orders` and `checkout-api`.

## Contents

- `atlas-export.json` — a `api-sentinel/AtlasExport/v1` manifest: the
  **structural** graph (Code Atlas owns it). `CALLS_ENDPOINT` edges are
  code/contract relations — they never prove a call happened.
- `services.jsonl` — the three logical services, each carrying its
  `code_atlas_identity` external ref (ADR-012 identity mapping).
- `dependencies.jsonl` — the **operational** graph the Sentinel observed:
  calls/errors/timeouts per edge per window. `payments → payments-db`
  carries 96 timeouts — the injected failure. These are observed edges,
  never structural claims.
- `observations.jsonl` — `payments` runtime observations showing the
  timeout anomaly (rising error counters).
- `changes.jsonl` — a `payments` deploy (`rel_payments_2.4.1`) that
  precedes the cascade — a *candidate* hypothesis, never a proven cause.

## What the oracle proves

- `correlate` identifies the observed `payments-db` dependency as the
  suspected origin (highest error+timeout count on the operational graph).
- Structural context is listed separately (`structural_edges`, kind
  `structural`) — the two graphs are never merged.
- `status` is `CORRELATED`, every hypothesis stays `HYPOTHESIZED` —
  temporal proximity + graph adjacency is *not* root cause.
- `unknowns` records what could not be resolved (e.g., when no Atlas
  export is configured, the structural side is an explicit unknown).

## Seeding (manual)

```bash
api-sentinel services --register checkout-api --namespace shop
api-sentinel services --register orders --namespace shop
api-sentinel services --register payments --namespace shop
# record the observed edges
api-sentinel deps --service-id svc_payments --record payments-db \
  --target-kind datastore --window-start 2026-01-01T00:00:00Z \
  --window-end 2026-01-01T00:10:00Z --calls 180 --timeouts 96
# configure atlas export in config.yaml → integrations.atlas.export
api-sentinel correlate --service-id svc_orders --json
```
